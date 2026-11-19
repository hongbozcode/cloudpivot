from functools import partial
from urllib.parse import urlsplit

from langchain_core.exceptions import ContextOverflowError
from langchain_core.language_models import BaseChatModel
from langchain_core.messages.utils import count_tokens_approximately
from langchain_openai import ChatOpenAI

from connection.models import PersonalModel
from connection.public_models import public_clients
from core import config as cfg


count_input_tokens = partial(count_tokens_approximately, chars_per_token=2.5)
WORKSPACE_CONTEXT = 'cloudpivot_workspace_context'


class CompatibleChatOpenAI(ChatOpenAI):
    input_budget: int | None = None

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        if self.input_budget is not None:
            messages = self._convert_input(input_).to_messages()
            tools = kwargs.get('tools')
            if count_input_tokens(messages, tools=tools) > self.input_budget:
                # Optional directory context can be rediscovered with file tools.
                messages = [message for message in messages if not message.additional_kwargs.get(WORKSPACE_CONTEXT)]
                if count_input_tokens(messages, tools=tools) > self.input_budget:
                    raise ContextOverflowError('模型输入超过配置的预算。')
                input_ = messages
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        # OpenAI and compatible providers use different names for the same limit.
        if urlsplit(self.openai_api_base or '').hostname != 'api.openai.com' and 'max_completion_tokens' in payload:
            payload['max_tokens'] = payload.pop('max_completion_tokens')
        return payload


def _personal_chat(model: PersonalModel) -> BaseChatModel:
    sync, async_client = public_clients(model.base_url)
    return CompatibleChatOpenAI(
        model=model.model_id, base_url=model.base_url, api_key=model.api_key,
        http_client=sync, http_async_client=async_client, openai_proxy='',
        use_responses_api=False, stream_usage=False, max_tokens=model.max_output_tokens,
        temperature=model.temperature if model.supports_temperature else None,
        timeout=60, input_budget=model.input_budget,
    )


def get_llm(model_name: str | PersonalModel | None = None):
    if isinstance(model_name, PersonalModel):
        return _personal_chat(model_name)
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

def get_vlm(model_name: str | PersonalModel | None = None):
    if isinstance(model_name, PersonalModel):
        return _personal_chat(model_name)
    cfg.require_env('OPENAI_API_KEY')
    vlm = ChatOpenAI(
            model=model_name or cfg.require_env('BASE_VLM')[0],
            base_url=cfg.MODEL_API_BASE_URL,
    )
    return vlm
