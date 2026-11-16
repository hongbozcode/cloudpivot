from langchain_openai import ChatOpenAI
from core import config as cfg

def get_llm():
    llm = ChatOpenAI(
            model=cfg.BASE_LLM,
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

def get_vlm():
    vlm = ChatOpenAI(
            model=cfg.BASE_VLM,
            base_url=cfg.MODEL_API_BASE_URL,
    )
    return vlm