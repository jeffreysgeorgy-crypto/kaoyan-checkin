# -*- coding: utf-8 -*-
"""
openJiuwen 环境冒烟测试：用当前配置的模型跑一个最小的 ReActAgent。
先编辑 .env 填好 API_KEY，然后运行：  py test_agent.py
"""
import asyncio
import logging
import os

# 静默 openjiuwen 导入时的 INFO 日志（与 agent.py 保持一致，输出更干净）
logging.disable(logging.WARNING)

from dotenv import load_dotenv

from openjiuwen.core.single_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

SYSTEM_PROMPT = "你是一个乐于助人的助手，请用一句话回答用户的问题。"


def main():
    load_dotenv()
    provider = os.getenv("MODEL_PROVIDER", "siliconflow")
    api_base = os.getenv("API_BASE", "https://api.siliconflow.cn/v1")
    api_key = os.getenv("API_KEY", "")
    model_name = os.getenv("MODEL_NAME", "Qwen/Qwen3-32B")
    verify_ssl = os.getenv("LLM_SSL_VERIFY", "false").lower() == "true"

    if not api_key or api_key.startswith("sk-xxxx"):
        print("❌ 请先编辑 agent/.env，把 API_KEY 换成你自己的 key")
        return

    print(f"正在用 {provider} / {model_name} 测试……")

    config = ReActAgentConfig()
    config.configure_model_client(
        provider=provider,
        api_key=api_key,
        api_base=api_base,
        model_name=model_name,
        verify_ssl=verify_ssl,
    )
    config.configure_prompt_template([{"role": "system", "content": SYSTEM_PROMPT}])
    config.configure_max_iterations(4)

    card = AgentCard(
        id="smoke_test",
        name="冒烟测试 Agent",
        description="用最小 ReAct 循环验证 openJiuwen + 模型连接是否正常。",
        input_params={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "用户问题"}},
            "required": ["query"],
        },
        output_params={
            "type": "object",
            "properties": {"answer": {"type": "string", "description": "回复"}},
        },
    )

    agent = ReActAgent(card)
    agent.configure(config)

    async def run():
        result = await agent.invoke({"query": "用一句话介绍你自己"})
        output = result.get("output", "")
        if result.get("result_type") == "error" or not output:
            print("❌ Agent 运行失败：", result)
            return
        print("✅ Agent 运行成功：", output)

    asyncio.run(run())


if __name__ == "__main__":
    main()
