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
        self.patches = {}  # Method → Patch
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
        confictMethods = self._get_sorted_method_pairs(state['bug_report'].method_pair_to_races)
        # key:(m1,m2), value: [(e1,e2),(e3,e4)]
        """
            不动点迭代直到集合为空
            :param initial_set: 初始集合
            :param process_func: 处理函数 (接收元素，返回要添加/删除的元素)
            :return: 最终收敛的集合
        """

        ##这块迭代写错了，应该是按照confictMethods顺序迭代运行
        ##confictMethods = set(confictMethods)

        ##尝试下这种行不行
        confictMethods = list(dict.fromkeys(confictMethods))
        #每次迭代选取一个方法对
        for cms in confictMethods:
            related_events = [event for race in state['bug_report'].method_pair_to_races[cms] for event in
                                (race.event1, race.event2)]  # 处理当前元素，返回需要添加/删除的元素
            related_vars = {event.variable for event in related_events}  # 如果属性名为 var
            suggest_polices = state['suggest_polices'] #这块可以笼统点，从cas或者voliatile、或者加锁
            policy_input = state['policies']#这块需要无歧义，详细。用结构化的格式输出。
            #根据这些信息，生成prompt，调用llm生成补丁和策略
            policies = self._generate_patch(
                cms,
                related_events,
                related_vars,
                suggest_polices=suggest_polices,
                policy_input=policy_input
            )
            # 更新以前不存在的修复策略
            policy_input.update(policies)

            # 处理受到影响的变量
            for v, p in policies.items():
                #如果是CAS,需要修改所有的涉及到的方法
                if "redefining property" in str(p).lower():
                    affected_methods = self._find_method_pairs_affected_by(v)
                    for affected_method in affected_methods:
                        # 对affected_method进行修复处理，让其按照现有的修复策略修复代码。

                        # 如果产生的补丁有冲突，调用大模型进行补丁合并
                        pass
                    # method_pairs.extend(affected_methods)

                    # 更新变量策略

        
        
        # """执行迭代修复流程"""
        # method_pairs = state.get("method_pairs", [])
        # related_events = state.get("related_events", {})
        # source_code = state.get("source_code", {})
        #
        # # 复制方法对列表，避免修改原始数据
        # method_pairs = method_pairs.copy()
        #
        # while method_pairs:
        #     # 选择一个方法对
        #     (m1, m2) = method_pairs.pop()
        #     # 获取相关变量
        #     if (m1, m2) in related_events:
        #         events = related_events[(m1, m2)]
        #         related_vars = {e1.variable for (e1, e2) in events} & {e2.variable for (e1, e2) in events}
        #     else:
        #         related_vars = set()
        #
        #     # 提取对应变量的保护策略
        #     policy_input = {v: self.var_policies.get(v, None) for v in related_vars}
        #
        #     # 生成补丁
        #     patches, policies = self._generate_patch(
        #         state=state,
        #         cms=(m1, m2),
        #         policy_input=policy_input,
        #         source_code=source_code
        #     )
        #
        #     # 处理策略变更
        #     for v, p in policies.items():
        #         if "redefining property" in str(p).lower():
        #             affected_pairs = self._find_method_pairs_affected_by(v, method_pairs, related_events)
        #             method_pairs.extend(affected_pairs)
        #
        #     # 更新变量策略
        #     self.var_policies.update(policies)
        #
        #     # 补丁合并与冲突检测
        #     for m in (m1, m2):
        #         if m in self.patches:
        #             # 合并补丁 (调用LLM)
        #             merged_patch = self._merge_patches(
        #                 existing_patch=self.patches[m],
        #                 new_patch=patch,
        #                 method_name=m,
        #                 source_code=source_code.get(m, "")
        #             )
        #
        #             # 检查冲突
        #             if self._has_conflict(self.patches[m], patch):
        #                 raise PatchConflictError(f"补丁冲突在方法: {m}")
        #
        #             self.patches[m] = merged_patch
        #         else:
        #             self.patches[m] = patch

        return None
        # return {
        #     "patches": self.patches,
        #     "var_policies": self.var_policies,
        #     "source_code": source_code
        # }

    def generate_patch(self, state, cms: ConfictMethod, related_events: list, related_vars: set,
                    suggest_polices: Dict[str, Any],
                    policy_input: Dict[str, Any],
                    source_code: Dict[str, str]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """方法体对应的源码"""
        m1 = cms.method1()
        m2 = cms.method2()
        method1_code = m1.source_code
        method2_code = m2.source_code
        
        """调用链对应的源码"""
        other_call_chain = dict()
        for event in related_events:
            # cal是方法
            for cal in event.call_chain:
                other_call_chain[cal] = state['bug_report'].method_to_method_info[cal]
        
        init_info = {}
        """初始化对应的源码"""
        for class_info in state['source_code'][m1.file_path]["classes"]:  # state['source_code']是个dict，key是文件路径，value是文件内容
            for e in related_events:
                if class_info['init_code'].class_name == e.class_name:
                    init_info[e.class_name] = class_info
        
        # 提示词
        messages = self.patch_generation_prompt.format_messages(
            method1_code=method1_code,
            method2_code=method2_code,
            policy_input=policy_input,
            suggest_polices=suggest_polices,
            other_call_chain=other_call_chain,
            init_info=init_info,
            related_vars=related_vars,
        )
        
        # 产生回应 - 调用本地ollama的qwen3:30b模型
        import requests
        import json
        
        # 添加自定义提示词
        custom_prompt = """
    请你作为一个专业的代码修复专家，根据提供的冲突方法和相关信息，生成高质量的补丁代码。
    请注意：
    1. 确保生成的补丁能够解决并发冲突问题
    2. 保持代码的可读性和性能
    3. 遵循最佳编程实践
    4. 提供清晰的修复策略说明

    现在请分析以下代码并生成相应的补丁：
    """
        
        # 如果messages是列表格式，添加系统消息
        if isinstance(messages, list):
            enhanced_messages = [{"role": "system", "content": custom_prompt}] + messages
        else:
            # 如果messages是字符串格式，直接拼接
            enhanced_messages = custom_prompt + "\n\n" + str(messages)
        
        try:
            # 构建请求数据
            payload = {
                "model": "qwen3:30b",
                "messages": enhanced_messages,
                "stream": False
            }
            
            # 发送请求到ollama API
            ollama_response = requests.post(
                "http://localhost:11434/api/chat",
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=300  # 5分钟超时
            )
            
            # 检查响应状态
            ollama_response.raise_for_status()
            
            # 解析响应，创建类似原来response的对象
            response_data = ollama_response.json()
            
            # 创建一个简单的response对象来模拟原来的response
            class Response:
                def __init__(self, content):
                    self.content = content
            
            response = Response(response_data.get('message', {}).get('content', ''))
            
        except requests.exceptions.RequestException as e:
            print(f"调用ollama模型时出现错误: {e}")
            raise
        except json.JSONDecodeError as e:
            print(f"解析ollama响应时出现错误: {e}")
            raise
        
        # 补丁解析，补丁如果冲突
        patches, policies = self._parse_patch_generation_response(response.content)
        
        method_to_info = {}
        method_to_info.update(other_call_chain)
        method_to_info.update(init_info)
        method_to_info[m1.method_name] = m1
        method_to_info[m2.method_name] = m2
        
        # 对import部分的处理
        for var, p in policies.items():
            # 判断是否p是使用CAS修复策略，如果是的话，需要提取文件初始化部分，然后对初始化部分进行修复
            if p == "CAS":
                files_init = {}
                for method, method_info in method_to_info.items():
                    files_init[method_info.file_path] = state['source_info'][method_info.file_path]["imports"]
                # 增加代码：对file_init对应的部分增加补丁
                # 增加代码：如果file_init对应的部分已经存在了补丁，进行补丁合并
        
        # 处理补丁
        for method, p in policies.items():
            if method_to_info[method].patch != None and method_to_info[method].patch != "":
                # 合并p和已有的补丁并存储
                pass
            else:
                method_to_info[method].patch = p
        
        return patches, policies

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
                                       related_events: Dict[Tuple[str, str], List[Tuple[Event, Event]]]) -> List[
        Tuple[str, str]]:
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
prompt_template = f"""You are a professional software development engineer who specializes in fixing bugs in concurrent programming.
Please analyze the following two methods, identify potential concurrency issues, and generate fix patches.
Additionally, please recommend updated protection policies for related variables.

Format instructions: Each ChangeLog group must start with a description of its included fixes. The group must then list one or more pairs of (OriginalCode, FixedCode) code snippets. Each OriginalCode snippet must list all consecutive original lines of code that must be replaced (including a few lines before and after the fixes), followed by the FixedCode snippet with all consecutive fixed lines of code that must replace the original lines of code (including the same few lines before and after the changes). In each pair, the OriginalCode and FixedCode snippets must start at the same source code line number N. Each listed code line, in both the OriginalCode and FixedCode snippets, must be prefixed with [N] that matches the line index N in the above snippets, and then be prefixed with exactly the same whitespace indentation as the original snippets above.

------------
ChangeLog:1@<file>
Fix:Description: <summary>.
OriginalCode{{method1_line_start}}-{{method1_line_end}}:
[{{line_num}}] <white space> <original code line>
[{{line_num}}] <white space> <original code line>
[{{line_num}}] <white space> <original code line>
FixedCode{{method1_line_start}}-{{method1_line_end}}:
[{{line_num}}] <white space> <fixed code line>
[{{line_num}}] <white space> <fixed code line>
[{{line_num}}] <white space> <fixed code line>
OriginalCode{{method2_line_start}}-{{method2_line_end}}:
[{{line_num}}] <white space> <original code line>
[{{line_num}}] <white space> <original code line>
[{{line_num}}] <white space> <original code line>
FixedCode{{method2_line_start}}-{{method2_line_end}}:
[{{line_num}}] <white space> <fixed code line>
[{{line_num}}] <white space> <fixed code line>
[{{line_num}}] <white space> <fixed code line>
..............
ChangeLog:K@<file>
Fix:Description: <summary>.
OriginalCode{{start_line}}-{{end_line}}:
[{{line_num}}] <white space> <original code line>
[{{line_num}}] <white space> <original code line>
FixedCode{{start_line}}-{{end_line}}:
[{{line_num}}] <white space> <fixed code line>
[{{line_num}}] <white space> <fixed code line>
OriginalCode{{start_line}}-{{end_line}}:
[{{line_num}}] <white space> <original code line>
FixedCode{{start_line}}-{{end_line}}:
[{{line_num}}] <white space> <fixed code line>
------------
Answer:

"""),
            HumanMessage(content="""
Method 1 Name: {{method1_name}}
Method 1 Code:
{{method1_code}}
Method 2 Name: {{method2_name}}
Method 2 Code:
{{method2_code}}
Current Protection Policy: {{policy_input}}
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