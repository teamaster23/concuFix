from typing import Dict, Any, List, Tuple, Set
from entity import ConfictMethod
from langchain.chat_models import ChatOpenAI
from langchain.prompts import ChatPromptTemplate
from langchain.schema import HumanMessage, SystemMessage
from initializer import Event

class RepairAgent():
    """修复智能体节点，整合补丁生成和合并逻辑"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        # self.llm = ChatOpenAI(
        #     model_name=self.config.get("llm_model", "gpt-4"),
        #     temperature=self.config.get("temperature", 0.2)
        # )
        
        self.var_policies = {}  # Variable → Policy
        self.patches = {}       # Method → Patch
        self.patch_generation_prompt = self._create_patch_generation_prompt()
        self.patch_merge_prompt = self._create_patch_merge_prompt()
    

        
    def _get_sorted_method_pairs(
        method_pair_to_races: Dict[ConfictMethod, List[Any]]
        ) -> List[ConfictMethod]:
        """按race数量降序返回方法对"""
        return sorted(
            method_pair_to_races.keys(),
            key=lambda k: len(method_pair_to_races[k]),
            reverse=True
        )

    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        confictMethods=self._get_sorted_method_pairs(state['bug_report'].method_pair_to_races)
        """
            不动点迭代直到集合为空
            :param initial_set: 初始集合
            :param process_func: 处理函数 (接收元素，返回要添加/删除的元素)
            :return: 最终收敛的集合
        """
        confictMethods = set(confictMethods)
        while confictMethods:
            next_set = set()
            for cms in confictMethods:
                related_events = [event for race in state['bug_report'].method_pair_to_races[cms] for event in (race.event1, race.event2)  ]  # 处理当前元素，返回需要添加/删除的元素
                related_vars = {event.variable for event in related_events}  # 如果属性名为 var
                policy_input=state['policies']
                updated_policies = self._generate_patch(
                    cms,
                    related_events,
                    related_vars,
                    policy_input=policy_input)
                
                
                            # 处理策略变更
                for v, p in updated_policies.items():
                    if "redefining property" in str(p).lower():
                        affected_pairs = self._find_method_pairs_affected_by(v, method_pairs, related_events)
                        method_pairs.extend(affected_pairs)
                           # 更新变量策略
                self.var_policies.update(updated_policies)
            
                
            # 更新当前集合（避免直接修改迭代中的集合）
            current_set = next_set
        
        
        """执行迭代修复流程"""
        method_pairs = state.get("method_pairs", [])
        related_events = state.get("related_events", {})
        source_code = state.get("source_code", {})
        
        # 复制方法对列表，避免修改原始数据
        method_pairs = method_pairs.copy()
        
        while method_pairs:
            # 选择一个方法对
            (m1, m2) = method_pairs.pop()
            # 获取相关变量
            if (m1, m2) in related_events:
                events = related_events[(m1, m2)]
                related_vars = {e1.variable for (e1, e2) in events} & {e2.variable for (e1, e2) in events}
            else:
                related_vars = set()
            
            # 提取对应变量的保护策略
            policy_input = {v: self.var_policies.get(v, None) for v in related_vars}
            
            # 生成补丁
            patch, updated_policies = self._generate_patch(
                method_pair=(m1, m2),
                policy_input=policy_input,
                source_code=source_code
            )
            
            # 处理策略变更
            for v, p in updated_policies.items():
                if "redefining property" in str(p).lower():
                    affected_pairs = self._find_method_pairs_affected_by(v, method_pairs, related_events)
                    method_pairs.extend(affected_pairs)
            
            # 更新变量策略
            self.var_policies.update(updated_policies)
            
            # 补丁合并与冲突检测
            for m in (m1, m2):
                if m in self.patches:
                    # 合并补丁 (调用LLM)
                    merged_patch = self._merge_patches(
                        existing_patch=self.patches[m],
                        new_patch=patch,
                        method_name=m,
                        source_code=source_code.get(m, "")
                    )
                    
                    # 检查冲突
                    if self._has_conflict(self.patches[m], patch):
                        raise PatchConflictError(f"补丁冲突在方法: {m}")
                    
                    self.patches[m] = merged_patch
                else:
                    self.patches[m] = patch
        
        return None
        # return {
        #     "patches": self.patches,
        #     "var_policies": self.var_policies,
        #     "source_code": source_code
        # }
    
    def _generate_patch(self, cms:ConfictMethod, related_events:list, related_vars:set,
                       policy_input: Dict[str, Any], 
                       source_code: Dict[str, str]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """调用LLM生成补丁和更新的策略"""
        m1 = cms.method1()
        m2 = cms.method2()
        
        method1_code = m1.source_code
        method2_code = m2.source_code
        
        #产生补丁
        messages = self.patch_generation_prompt.format_messages(
            method1_name=m1,
            method1_code=method1_code,
            method2_name=m2,
            method2_code=method2_code,
            policy_input=policy_input
        )
        response = self.llm(messages)
        m1_pactch,m2_patch,policies = self._parse_patch_generation_response(response.content)
        if len(m1.patch)!=0:
            self._merge_patches()
        if len(m2.patch)!=0:
            self._merge_patches()
            
        
        #return patch, policies
    
    def _merge_patches(self, existing_patch: Dict[str, Any], 
                     new_patch: Dict[str, Any], 
                     method_name: str,
                     source_code: str) -> Dict[str, Any]:
        """调用LLM合并两个补丁"""
        messages = self.patch_merge_prompt.format_messages(
            method_name=method_name,
            method_code=source_code,
            existing_patch=existing_patch,
            new_patch=new_patch
        )
        
        response = self.llm(messages)
        merged_patch = self._parse_patch_merge_response(response.content)
        return merged_patch
    
    def _has_conflict(self, patch1: Dict[str, Any], patch2: Dict[str, Any]) -> bool:
        """简单检查两个补丁是否冲突"""
        # 实际实现可以更复杂，例如分析代码变更
        return False  # 简化为示例
    
    def _find_method_pairs_affected_by(self, variable: str, 
                                      method_pairs: List[Tuple[str, str]], 
                                      related_events: Dict[Tuple[str, str], List[Tuple[Event, Event]]]) -> List[Tuple[str, str]]:
        """查找受变量影响的方法对"""
        affected = []
        for pair in method_pairs:
            if pair in related_events:
                for e1, e2 in related_events[pair]:
                    if e1.variable == variable or e2.variable == variable:
                        affected.append(pair)
                        break
        return affected
    
    def _create_patch_generation_prompt(self) -> ChatPromptTemplate:
        """创建用于生成补丁的提示模板"""
        return ChatPromptTemplate.from_messages([
            SystemMessage(content="""
你是一个专业的软件开发工程师，擅长修复并发编程中的bug。
请分析以下两个方法，找出可能的并发问题，并生成修复补丁。
同时，请为相关变量推荐更新的保护策略。

返回格式:
{
    "patches": {
        "method1_name": "修复后的方法1代码",
        "method2_name": "修复后的方法2代码"
    },
    "updated_policies": {
        "variable1": "新的保护策略",
        "variable2": "新的保护策略"
    },
    "explanation": "对修复的解释"
}
"""),
            HumanMessage(content="""
方法1名称: {method1_name}
方法1代码:
{method1_code}

方法2名称: {method2_name}
方法2代码:
{method2_code}

当前保护策略: {policy_input}
""")
        ])
    
    def _create_patch_merge_prompt(self) -> ChatPromptTemplate:
        """创建用于合并补丁的提示模板"""
        return ChatPromptTemplate.from_messages([
            SystemMessage(content="""
你是一个专业的软件开发工程师，擅长合并代码变更。
请合并以下两个补丁，并确保合并后的代码正确且没有冲突。

返回格式:
{
    "merged_patch": "合并后的代码",
    "explanation": "对合并的解释",
    "has_conflict": false,
    "conflict_details": ""
}
"""),
            HumanMessage(content="""
方法名称: {method_name}
原始代码:
{method_code}

现有补丁:
{existing_patch}

新补丁:
{new_patch}
""")
        ])
    
    def _parse_patch_generation_response(self, response: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """解析LLM响应，提取补丁和策略"""
        # 实际实现需要更健壮的解析逻辑
        try:
            import json
            data = json.loads(response)
            return data["patches"], data["updated_policies"]
        except:
            # 简化回退逻辑
            return {
                "method1": f"# 生成的补丁 for method1\n{response}",
                "method2": f"# 生成的补丁 for method2\n{response}"
            }, {}
    
    def _parse_patch_merge_response(self, response: str) -> Dict[str, Any]:
        """解析LLM响应，提取合并后的补丁"""
        # 实际实现需要更健壮的解析逻辑
        try:
            import json
            return json.loads(response)
        except:
            # 简化回退逻辑
            return {
                "merged_patch": f"# 合并的补丁\n{response}",
                "explanation": "合并后的补丁",
                "has_conflict": False,
                "conflict_details": ""
            }

class PatchConflictError(Exception):
    """补丁冲突异常"""
    pass    