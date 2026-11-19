from langchain_openai import ChatOpenAI
from core import config as cfg

def get_llm(model_name: str | None = None):
    cfg.require_env('OPENAI_API_KEY')
    llm = ChatOpenAI(
            model=model_name or cfg.require_env('BASE_LLM')[0],
            base_url=cfg.MODEL_API_BASE_URL,
            temperature=0.1,
        )
    return llm

def get_small_llm():
    llm = ChatOpenAI(
            model=cfg.SMALL_LLM,
            base_url=cfg.MODEL_API_BASE_URL,
            temperature=0.1,
        )
    return llm

def get_vlm(model_name: str | None = None):
    cfg.require_env('OPENAI_API_KEY')
    vlm = ChatOpenAI(
            model=model_name or cfg.require_env('BASE_VLM')[0],
            base_url=cfg.MODEL_API_BASE_URL,
    )
    return vlm
