#from langgraph import Graph
import langgraph
#from langgraph.graph import StateGraph

from initializer import Initializer
from repair_agent import RepairAgent
# from verification_agent import VerificationAgent
# from patch_applier import PatchApplier
import json
import sys

from langgraph.graph import END, StateGraph

from typing import TypedDict

# class State(TypedDict):
#     iteration: int
#     max_iterations: int
#     verification_passed: bool


def verification_routing(state:dict) -> str:
    # Deleted: if state.get("verification_passed", False):
    # Deleted:     return "to_patch_applier"
    # Deleted: elif state.get("iteration", 0) >= state.get("max_iterations", 5):
    # Deleted:     return "exit"
    # Deleted: else:
    # Deleted:     # state["iteration"]= state["iteration"]+1
    # Deleted:     return "to_repair_agent"
    pass

def main():


    # 加载配置
    try:
        with open("config.json", 'r') as f:
            config = json.load(f)
    except FileNotFoundError:
        print("配置文件 config.json 不存在，请创建该文件")
        sys.exit(1)
    builder = StateGraph(state_schema=dict)  # 状态是 dict 类型


    state = {
        "iteration": 0,
        "max_iterations": 3,
        "verification_passed": True,
        "method_pairs": [...],
        "related_events": {...},
        "source_code": {...},
        "bug_report": None
    }
    # state = State({
    #     "iteration": 0,
    #     "max_iterations": 3,
    #     "verification_passed": False
    # })


    # 添加节点
    initializer = Initializer(config)
    repair_agent = RepairAgent(config)
    # Deleted: verification_agent = VerificationAgent(config)
    # Deleted: patch_applier = PatchApplier(config)

    builder.add_node("initializer", lambda state: initializer.process(state))
    builder.add_node("repair_agent", lambda state: repair_agent.process(state) )
    # Deleted: builder.add_node("verification_agent", lambda state: verification_agent.process(state))
    # Deleted: builder.add_node("patch_applier", lambda state: patch_applier.process(state))

    # 普通边
    builder.add_edge("initializer", "repair_agent")
    # Deleted: builder.add_edge("repair_agent", "verification_agent")


    # 条件边：验证后选择路由
    # Deleted: builder.add_conditional_edges(
    # Deleted:     "verification_agent",
    # Deleted:     verification_routing,  # 没有空格！
    # Deleted:     {
    # Deleted:         "to_patch_applier": "patch_applier",
    # Deleted:         "to_repair_agent": "repair_agent",
    # Deleted:         "exit": END  # ✅ 表示流程结束
    # Deleted:     }
    # Deleted: )
    # 设定入口和结束节点
    builder.set_entry_point("initializer")
    # Deleted: builder.set_finish_point("patch_applier")





    graph = builder.compile()

    print("--- Invoking Graph ---")
    # The graph's entry point is 'initializer', so it will run first,
    # and its output will be passed to 'repair_agent'.
    final_state = graph.invoke(state)

    print("\n--- Final State ---")
    # The final state will contain the output from the last node that ran, which is 'repair_agent'.
    # Since repair_agent returns None, this will likely be None.
    # The patches are printed from within repair_agent.py itself.
    print(final_state)
    print("-------------------")

    # # 运行图
    # try:
    #     result = graph.run()
    # except Exception as e:
    #     print(f"运行修复流程时出错: {str(e)}")
    #     sys.exit(1)

    # # 输出结果
    # if result.get("applied", False):
    #     print("\n=== 修复成功 ===")
    #     print(f"成功应用了 {len(result.get('patches_applied', {}))} 个补丁")
    #     print(f"修复说明: {result.get('message', '')}")
    # else:
    #     print("\n=== 修复失败 ===")
    #     print(f"原因: {result.get('message', '未知原因')}")
    #     if "feedback" in result:
    #         print("详细反馈:")
    #         for line in result["feedback"]:
    #             print(f"- {line}")

if __name__ == "__main__":
    main()