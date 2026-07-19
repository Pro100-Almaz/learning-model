from langchain_openai import ChatOpenAI

import config
from statistics_analyst.schemas import AnalysisOut
from statistics_analyst.prompts import analyst_system, build_analyst_input

_llm = ChatOpenAI(model=config.ANALYST_MODEL, temperature=0.5, timeout=60)
_analyst = _llm.with_structured_output(AnalysisOut, method="function_calling")
#with_structured_output(AnalysisOut) is what forces the model to return your pydantic shape.
#Use method="function_calling" - that's the method that works in this repo (see the extractor).

def analyze(report, language) -> AnalysisOut | None:
    if language not in config.SUPPORTED_LANGUAGES: raise ValueError(f"Language {language} is not supported")
    messages = [
        ("system", analyst_system(language)),
        ("human", build_analyst_input(report))
    ]

    try:
        result = _analyst.invoke(messages)
        return result
    except Exception as e:
        print(f"Analysis failed for {report}, {e}")
        return None