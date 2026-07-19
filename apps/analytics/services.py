"""Business logic for the analytics app.

These services compute per-tag performance statistics and weak-tag
recommendations based on completed test attempts. They are pure
functions so they can be reused from views, management commands, or
tasks without DRF context.
"""

from __future__ import annotations

from typing import Iterable, Any

from django.db.models import Count, Q

import config
from apps.content.models import Lesson, Tag


def _percent(correct: int, total: int) -> float:
    """Null-safe percent: returns 0.0 when total is 0."""
    if not total:
        return 0.0
    return round((correct / total) * 100, 1)


def _serialize_tag(tag: Tag) -> dict:
    return {"id": tag.id, "name": tag.name, "slug": tag.slug}


def _serialize_lesson_summary(lesson: Lesson, *, completed: bool = False) -> dict:
    return {
        "id": lesson.id,
        "title": lesson.title,
        "order": lesson.order,
        "duration_sec": lesson.duration_sec,
        "completed": completed,
    }


def compute_tag_stats(user) -> list[dict]:
    """Per-tag correct/total/percent for the given user.

    Counts AttemptAnswer rows where the attempt belongs to ``user`` and
    is completed. A row matches a Tag when its question is tagged with
    that Tag.

    Returns a list of dicts shaped per the openapi.yaml ``TagStat``
    schema. Ordered by Tag.name.
    """
    # The reverse relation from Question -> AttemptAnswer uses Django's
    # default related accessor (``attemptanswer``) because the FK on
    # ``AttemptAnswer.question`` has no explicit related_name.
    total_filter = Q(
        questions__attemptanswer__attempt__student=user,
        questions__attemptanswer__attempt__is_completed=True,
    )
    correct_filter = total_filter & Q(questions__attemptanswer__is_correct=True)

    qs = Tag.objects.annotate(
        total=Count("questions__attemptanswer", filter=total_filter),
        correct=Count("questions__attemptanswer", filter=correct_filter),
    ).order_by("name")

    stats: list[dict] = []
    for tag in qs:
        total = tag.total or 0
        correct = tag.correct or 0
        stats.append(
            {
                "tag": _serialize_tag(tag),
                "correct": correct,
                "total": total,
                "percent": _percent(correct, total),
            }
        )
    return stats


def _lessons_for_tag_ids(tag_ids: Iterable[int]) -> dict[int, list[Lesson]]:
    """Group ordered, deduplicated lessons by tag id.

    A lesson is associated with a tag when it has at least one question
    tagged with that tag. Lessons are returned ordered by
    ``Lesson.order`` and deduplicated per-tag.
    """
    grouped: dict[int, list[Lesson]] = {tag_id: [] for tag_id in tag_ids}
    if not grouped:
        return grouped

    lessons = (
        Lesson.objects.filter(questions__tags__in=grouped.keys())
        .prefetch_related("questions__tags")
        .distinct()
        .order_by("order", "id")
    )
    seen: dict[int, set[int]] = {tag_id: set() for tag_id in grouped}
    for lesson in lessons:
        lesson_tag_ids: set[int] = set()
        for question in lesson.questions.all():
            for tag in question.tags.all():
                lesson_tag_ids.add(tag.id)
        for tag_id in lesson_tag_ids:
            if tag_id in grouped and lesson.id not in seen[tag_id]:
                grouped[tag_id].append(lesson)
                seen[tag_id].add(lesson.id)
    return grouped


def compute_recommendations(user) -> list[dict]:
    """Recommendations for tags where percent < 50.

    Empty list when the user has no weak tags (or no answers at all).
    Each entry contains the tag, the percent and a deduplicated list of
    LessonSummary dicts ordered by ``Lesson.order``.
    """
    stats = compute_tag_stats(user)
    weak = [s for s in stats if s["total"] > 0 and s["percent"] < 50]
    if not weak:
        return []

    tag_ids = [s["tag"]["id"] for s in weak]
    lessons_by_tag = _lessons_for_tag_ids(tag_ids)

    recommendations: list[dict] = []
    for s in weak:
        tag_id = s["tag"]["id"]
        lessons = lessons_by_tag.get(tag_id, [])
        recommendations.append(
            {
                "tag": s["tag"],
                "percent": s["percent"],
                "lessons": [_serialize_lesson_summary(lesson) for lesson in lessons],
            }
        )
    return recommendations


_LADDER_VERDICTS = ("gap", "solid", "mastered")


def compute_ladder_verdict_distribution(module=None) -> list[dict]:
    """Per-module, per-topic distribution of chapter-ladder verdicts.

    The calibration feedback loop from ``07_Chapter_Ladder_Spec.md``: across all
    completed ``ChapterLadderSession``s, what fraction of students land
    ``gap`` / ``solid`` / ``mastered`` on each topic. A topic that is ~100% gap is
    either genuinely hard or its medium rung is mistuned; ~100% mastered means the
    rungs are too easy.

    Pass a ``Module`` to scope to one chapter. Returns a list of
    ``{module_id, module_title, topics: [{tag_id, tag_slug, tag_name, counts,
    total, fractions}]}``, ordered by module then topic slug.
    """
    # Imported here (not at module top) so analytics stays importable without the
    # roadmap app and to avoid an app-loading cycle.
    from apps.content.models import Tag
    from apps.roadmap.models import ChapterLadderSession

    sessions = ChapterLadderSession.objects.filter(is_complete=True).select_related("module")
    if module is not None:
        sessions = sessions.filter(module=module)

    per_module: dict[int, dict] = {}
    for session in sessions:
        state = session.state or {}
        bucket = per_module.setdefault(
            session.module_id,
            {"module_id": session.module_id, "module_title": session.module.title, "topics": {}},
        )
        for tag_id_str, topic_state in state.get("per_topic", {}).items():
            verdict = topic_state.get("verdict")
            if verdict not in _LADDER_VERDICTS:
                continue
            counts = bucket["topics"].setdefault(int(tag_id_str), {v: 0 for v in _LADDER_VERDICTS})
            counts[verdict] += 1

    all_tag_ids = {tid for bucket in per_module.values() for tid in bucket["topics"]}
    tags = {t.id: t for t in Tag.objects.filter(id__in=all_tag_ids)}

    result: list[dict] = []
    for bucket in per_module.values():
        topics = []
        for tag_id, counts in bucket["topics"].items():
            total = sum(counts.values())
            tag = tags.get(tag_id)
            topics.append(
                {
                    "tag_id": tag_id,
                    "tag_slug": tag.slug if tag else None,
                    "tag_name": tag.name if tag else None,
                    "counts": counts,
                    "total": total,
                    "fractions": {
                        v: round(counts[v] / total, 3) if total else 0.0 for v in _LADDER_VERDICTS
                    },
                }
            )
        topics.sort(key=lambda t: t["tag_slug"] or "")
        result.append(
            {
                "module_id": bucket["module_id"],
                "module_title": bucket["module_title"],
                "topics": topics,
            }
        )
    result.sort(key=lambda m: m["module_id"])
    return result


def build_post_topic_results(user) -> dict[int, dict]:
    """Latest completed post-topic (``micro``) exam result, per topic.

    Task-1 source of truth for the analytics report. For each topic (a
    ``content.Tag``) this returns the student's *most recent completed* ``micro``
    exam attempt, mapped to the topic via ``attempt.test.lesson.tag``.

    Unlike :func:`compute_tag_stats` (lifetime accuracy across *every* attempt),
    this reads only the post-module micro exams and keeps just the latest per
    topic — the honest "how did they do on the exam after the module" signal.

    Returns ``{tag_id: {tag, post_score, correct, total, finished_at}}`` keyed by
    tag id so callers (bucketing, the report) get O(1) lookups. Empty dict when
    the student has no completed micro attempts. Attempts whose test has no
    lesson, or whose lesson has no tag, can't be attributed to a topic and are
    skipped.
    """
    # Local import: keeps analytics importable without assessments loaded and
    # avoids any app-loading ordering surprises (assessments never imports us).
    from apps.assessments.models import TestAttempt

    attempts = (
        TestAttempt.objects.filter(
            student=user,
            is_completed=True,
            source="test",
            test__type="micro",
        )
        # Walk test -> lesson -> tag in the initial query, not per-row, so the
        # loop below fires zero extra queries (no N+1).
        .select_related("test", "test__lesson", "test__lesson__tag")
        # correct/total in the same query. distinct=True is required: counting
        # two different multi-valued relations (test's questions AND this
        # attempt's answers) in one query otherwise cross-joins and inflates
        # both counts.
        .annotate(
            total=Count("test__questions", distinct=True),
            correct=Count("answers", filter=Q(answers__is_correct=True), distinct=True),
        )
        # Ascending finish time: a more recent attempt for the same topic
        # overwrites the earlier dict entry, leaving the latest per topic.
        .order_by("finished_at")
    )

    results: dict[int, dict] = {}
    for attempt in attempts:
        lesson = attempt.test.lesson
        tag = lesson.tag if lesson else None
        if tag is None:
            # Micro exam not linked to a topic (nullable Test.lesson / Lesson.tag):
            # there's nothing to attribute the score to, so skip it.
            continue
        results[tag.id] = {
            "tag": _serialize_tag(tag),
            "post_score": attempt.score,
            "correct": attempt.correct,
            "total": attempt.total,
            "finished_at": attempt.finished_at,
        }
    return results

def classify_topics(post_results: dict[int, dict]) -> dict[str, list[dict]]:
    #three lists are made for storing results of the students' according to their scores from the exam
    weak, improving, solid = [], [], []
    for entry in post_results.values(): #iterating through the values
        if entry["post_score"] < config.WEAK_BELOW:
            weak.append(entry)
        elif entry["post_score"] >= config.SOLID_MIN:
            solid.append(entry)
        else:
            improving.append(entry)
    weak.sort(key = lambda entry: entry["post_score"])
    final_dict = {"weak": weak, "improving": improving, "solid": solid}
    return final_dict

def build_student_report(user) -> dict[str, dict[str, list[dict]] | list[Any]]:
    '''
    Returns the student's analysis of the near_miss professions, the professions already qualified for, universities the student eligible to apply
    Also returns the bucket of the topics and careers math/university projection -> degrades if no mock
    '''
    results = build_post_topic_results(user) #getting the results of the user
    buckets = classify_topics(results) #identifying weak, improved, solid topics of the user
    weak_entries = buckets["weak"] #getting the weak entries
    list_of_weak_entries = []
    for entry in weak_entries:
        list_of_weak_entries.append(entry["tag"]["id"])
    lessons_by_tag = _lessons_for_tag_ids(list_of_weak_entries) #fetching the tag ids of the weak entries
    recommendations = []
    for entry in weak_entries:
        lessons = []
        lessons_of_entry = lessons_by_tag.get(entry["tag"]["id"], [])
        for lesson in lessons_of_entry:
            serialized_lesson_of_entry = _serialize_lesson_summary(lesson)
            lessons.append(serialized_lesson_of_entry)
        dict_of_serialized_lesson_of_entry = {
            "tag": entry["tag"],
            "post_score": entry["post_score"],
            "lessons": lessons
        }
        recommendations.append(dict_of_serialized_lesson_of_entry)

    from apps.careers.services import calculate_grant, near_miss_grants, NoMockError
    profile = getattr(user, "profile", None)
    target_math = profile.target_math_score if profile else None
    try:
        result = calculate_grant(user)
        current_math = result["math_score"]
        predicted = result["predicted_score"]
        qualifying = result["qualifying_grants"]
        near_miss = near_miss_grants(predicted)
    except NoMockError:
        current_math = None
        qualifying = []
        near_miss = []
    if current_math is not None and target_math is not None:
        gap = target_math - current_math
    else:
        gap = None
    math = {
        "current_math": current_math,
        "target_math": target_math,
        "gap": gap
    }
    universities = {
        "qualifying": qualifying,
        "near_miss": near_miss
    }

    stat_analysis = {
        "buckets": buckets,
        "recommendations": recommendations,
        "math": math,
        "universities": universities
    }

    return stat_analysis
