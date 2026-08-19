"""UBT blueprint-engine endpoints, under ``/api/v1/ubt/``.

    GET  /topics/      the catalogue: what can be generated, at which difficulties
    POST /preview/     generate WITHOUT publishing -- the API form of preview.py
    POST /questions/   generate AND publish, every language, atomically
    GET  /coverage/    can the bank be served in Kazakh yet?

All four are SYNCHRONOUS, and that is the point rather than an oversight. The
MAIQE endpoints next door need a job row, a Celery task and an SSE stream
because every question costs several LLM round trips. Here generation is pure
Python (~23ms per question, measured over 4800) and localization is a dict
lookup, so a batch of 20 returns in about a second. Wrapping that in job
plumbing would import all of MAIQE's complexity to solve a problem determinism
already removed.

`count` is capped at 50 per request. That cap, not a queue, is what bounds the
work a single request can do.
"""

from __future__ import annotations

import logging

from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.assessments import figures
from apps.assessments.models import Question
from apps.generation.ubt_serializers import (
    UbtCoverageSerializer,
    UbtGenerateRequestSerializer,
    UbtPreviewResponseSerializer,
    UbtPublishResponseSerializer,
    UbtTopicSerializer,
)
from ubt_question_engine import i18n
from ubt_question_engine.generator import generate_question
from ubt_question_engine.loader import BlueprintError, list_topics, topic_meta
from ubt_question_engine.localize import ENGINE_LANGUAGES, localize
from ubt_question_engine.params import GenerationError
from apps.generation.ubt_planning import SEED_SPACE  # noqa: F401  (re-exported for tests)
from apps.generation.ubt_planning import build_plan as _plan
from apps.generation.ubt_planning import resolve_languages as _resolve_languages
from apps.generation.ubt_planning import resolve_topics as _resolve_topics  # noqa: F401
from ubt_question_engine.publisher import PublishError, publish, publishable_languages

logger = logging.getLogger("apps.generation")

class UbtTopicListView(APIView):
    """The catalogue. Reads blueprints off disk; touches no table."""

    permission_classes = [IsAuthenticated]
    serializer_class = UbtTopicSerializer

    @extend_schema(tags=["UBT"], responses=UbtTopicSerializer(many=True))
    def get(self, request):
        return Response(
            UbtTopicSerializer([topic_meta(t) for t in list_topics()], many=True).data
        )


class UbtPreviewView(APIView):
    """Generate without publishing, so an item can be judged before it exists.

    Staff-only because the response exposes `is_correct` and `misconception` for
    every option -- exactly the fields a student must never see. This is the
    review tool, not a serving endpoint.
    """

    permission_classes = [IsAdminUser]
    serializer_class = UbtPreviewResponseSerializer

    @extend_schema(
        tags=["UBT"],
        request=UbtGenerateRequestSerializer,
        responses=UbtPreviewResponseSerializer,
    )
    def post(self, request):
        payload = UbtGenerateRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        languages = _resolve_languages(data.get("languages"))
        plan, base_seed = _plan(data)

        items, failures = [], []
        for topic, difficulty, seed in plan:
            try:
                base = generate_question(topic, difficulty=difficulty, seed=seed)
                for language in languages:
                    state = localize(base, language)
                    items.append(
                        {
                            "topic": state["topic"],
                            "display_name": state["display_name"],
                            "mode": state["mode"],
                            "difficulty": state["difficulty"],
                            "seed": state["seed"],
                            "content_hash": state["content_hash"],
                            "language": language,
                            "instruction": state["instruction"],
                            "latex": state["latex"],
                            "text": state["text"],
                            "answer_latex": state["answer_latex"],
                            "figure": figures.figure_for_mode(
                                state["topic"], state["mode"], language
                            ),
                            "options": [
                                {
                                    "latex": choice["latex"],
                                    "is_correct": choice["is_correct"],
                                    "misconception": choice["distractor_id"],
                                }
                                for choice in state["answer_options"]
                            ],
                        }
                    )
            except (GenerationError, BlueprintError, LookupError) as error:
                # One bad topic must not sink a 58-topic sweep: a survey that
                # reports three failures is worth far more than one that dies.
                failures.append(f"{topic} d{difficulty} seed {seed}: {error}")

        return Response(
            UbtPreviewResponseSerializer(
                {
                    "requested": len(plan),
                    "generated": len(items),
                    "seed": base_seed,
                    "languages": languages,
                    "items": items,
                    "failures": failures,
                }
            ).data
        )


class UbtPublishView(APIView):
    """Generate and publish. Every language of an item in one transaction."""

    permission_classes = [IsAdminUser]
    serializer_class = UbtPublishResponseSerializer

    @extend_schema(
        tags=["UBT"],
        request=UbtGenerateRequestSerializer,
        responses=UbtPublishResponseSerializer,
    )
    def post(self, request):
        payload = UbtGenerateRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        languages = _resolve_languages(data.get("languages"))
        plan, base_seed = _plan(data)

        items, failures, question_ids = [], [], []
        published = duplicates = 0

        for topic, difficulty, seed in plan:
            try:
                base = generate_question(topic, difficulty=difficulty, seed=seed)
                states = {lang: localize(base, lang) for lang in languages}
                results = publish(states)
            except (GenerationError, PublishError, BlueprintError, LookupError) as error:
                failures.append(f"{topic} d{difficulty} seed {seed}: {error}")
                continue

            was_duplicate = all(r["was_duplicate"] for r in results.values())
            duplicates += was_duplicate
            published += not was_duplicate
            ids = {lang: r["question_id"] for lang, r in results.items()}
            question_ids.extend(ids.values())
            items.append(
                {
                    "topic": base["topic"],
                    "mode": base["mode"],
                    "difficulty": base["difficulty"],
                    "seed": base["seed"],
                    "content_hash": base["content_hash"],
                    "question_ids": ids,
                    "was_duplicate": was_duplicate,
                }
            )

        if failures:
            logger.warning(
                "UBT publish: %s of %s items failed; first: %s",
                len(failures), len(plan), failures[0],
            )

        questions = (
            Question.objects.filter(pk__in=question_ids).prefetch_related("options")
        )
        return Response(
            UbtPublishResponseSerializer(
                {
                    "requested": len(plan),
                    "published": published,
                    "duplicates": duplicates,
                    "failed": len(failures),
                    "seed": base_seed,
                    "languages": languages,
                    "items": items,
                    "failures": failures,
                    # The queryset, not its .data: `questions` is a nested
                    # QuestionPublicSerializer, so handing it pre-serialized
                    # dicts makes it try to serialize them a second time.
                    "questions": questions,
                }
            ).data
        )


class UbtCoverageView(APIView):
    """Can the bank be served in each language yet?

    The operational question behind every failed publish: `no_translations` from
    the endpoints above means run the build script, and this says how much of it
    is left.
    """

    permission_classes = [IsAdminUser]
    serializer_class = UbtCoverageSerializer

    @extend_schema(tags=["UBT"], responses=UbtCoverageSerializer)
    def get(self, request):
        coverage = i18n.coverage()
        return Response(
            UbtCoverageSerializer(
                {
                    "translatable_strings": len(i18n.translatable()),
                    "coverage": {k: list(v) for k, v in coverage.items()},
                    "publishable_languages": list(publishable_languages()),
                    "missing_examples": {
                        language: list(i18n.missing(language))[:5]
                        for language in ENGINE_LANGUAGES
                    },
                }
            ).data
        )
