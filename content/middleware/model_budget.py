from deepagents.middleware.summarization import SummarizationMiddleware
from langchain.agents.middleware import ModelRequest
from langchain_core.messages import AnyMessage, HumanMessage

from connection.llm import count_input_tokens


class ModelBudget(SummarizationMiddleware):
    name = 'SummarizationMiddleware'

    def __init__(self, model, backend, budget: int):
        super().__init__(
            model=model, backend=backend, trigger=('tokens', budget),
            keep=('tokens', budget // 3), trim_tokens_to_summarize=budget // 2,
            token_counter=count_input_tokens,
        )
        self.budget = budget

    def _input_budget(self, request: ModelRequest) -> int:
        provider_limit = super()._input_budget(request)
        return self.budget if provider_limit is None else min(self.budget, provider_limit)

    def _determine_cutoff_index(self, messages: list[AnyMessage]) -> int:
        cutoff = super()._determine_cutoff_index(messages)
        current_request = next((
            index for index in range(len(messages) - 1, -1, -1)
            if isinstance(messages[index], HumanMessage)
            and messages[index].additional_kwargs.get('lc_source') != 'summarization'
        ), len(messages))
        return min(cutoff, current_request)
