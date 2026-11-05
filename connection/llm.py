from langchain_openai import ChatOpenAI
# 用于测试
from dotenv import load_dotenv
load_dotenv()  # 默认加载当前目录下的 .env 文件

def get_llm():
    llm = ChatOpenAI(
            model="Qwen/Qwen3-Next-80B-A3B-Instruct",
            base_url="https://api.siliconflow.cn/v1",
        )
    return llm

if __name__ == '__main__':
    model = get_llm()
    print(model.invoke("hello world"))