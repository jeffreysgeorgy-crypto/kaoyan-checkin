# -*- coding: utf-8 -*-
"""
openJiuwen 环境冒烟测试：用 SiliconFlow 跑一个最小的 WorkflowAgent。
先编辑 .env 填好 API_KEY，然后运行：  py test_agent.py
"""
import os
import asyncio
from dotenv import load_dotenv

load_dotenv()

from openjiuwen.core.workflow import (
    Start, End, LLMComponent, LLMCompConfig, generate_workflow_key,
)
from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.legacy import WorkflowAgentConfig
from openjiuwen.core.application.workflow_agent import WorkflowAgent
from openjiuwen.core.workflow import Workflow, WorkflowCard


def main():
    provider = os.getenv("MODEL_PROVIDER", "siliconflow")
    api_base = os.getenv("API_BASE", "https://api.siliconflow.cn/v1")
    api_key = os.getenv("API_KEY", "")
    model_name = os.getenv("MODEL_NAME", "Qwen/Qwen3-32B")

    if not api_key or api_key.startswith("sk-xxxx"):
        print("❌ 请先编辑 agent/.env，把 API_KEY 换成你自己的 key")
        return

    print(f"正在用 {provider} / {model_name} 测试……")

    model_client_config = ModelClientConfig(
        client_provider=provider,
        api_key=api_key,
        api_base=api_base,
        verify_ssl=os.getenv("LLM_SSL_VERIFY", "false").lower() == "true",
    )
    model_config = ModelRequestConfig(model=model_name)

    workflow_card = WorkflowCard(
        id="smoke_test",
        name="smoke_test",
        version="1.0",
        description="冒烟测试",
        input_params={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "输入"}},
            "required": ["query"],
        },
    )

    flow = Workflow(card=workflow_card)
    start = Start()
    end = End({"responseTemplate": "输出：{{output}}"})
    llm = LLMComponent(LLMCompConfig(
        model_client_config=model_client_config,
        model_config=model_config,
        template_content=[
            {"role": "system", "content": "你是一个乐于助人的助手。"},
            {"role": "user", "content": "{{query}}"},
        ],
        response_format={"type": "json"},
        output_config={
            "type": "object",
            "properties": {"output": {"type": "string", "description": "回复内容"}},
            "required": ["output"],
        },
    ))

    flow.set_start_comp("start", start, inputs_schema={"query": "${query}"})
    flow.add_workflow_comp("llm", llm, inputs_schema={"query": "${start.query}"})
    flow.set_end_comp("end", end, inputs_schema={"output": "${llm.output}"})
    flow.add_connection("start", "llm")
    flow.add_connection("llm", "end")

    Runner.resource_mgr.add_workflow(
        WorkflowCard(id=generate_workflow_key(flow.card.id, flow.card.version)),
        lambda: flow,
    )

    agent_config = WorkflowAgentConfig(id="smoke_agent", version="0.1.0", description="smoke")
    agent = WorkflowAgent(agent_config)
    agent.add_workflows([flow])

    async def run():
        result = await Runner.run_agent(agent, {"query": "用一句话介绍你自己"})
        out = result.get("output").result
        print("✅ Agent 运行成功：", out.get("response"))

    asyncio.run(run())


if __name__ == "__main__":
    main()
