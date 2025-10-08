from typing import Dict, Any, List, Tuple, Set
from entity import ConfictMethod
from langchain_community.chat_models import ChatOpenAI
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

    @staticmethod
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
            patches, policies = self.generate_patch(
                state,
                cms,
                related_events,
                related_vars,
                suggest_polices=suggest_polices,
                policy_input=policy_input,
                source_code=state['source_code']
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
        m1 = cms.method1
        m2 = cms.method2
        method1_code = m1.source_code
        method2_code = m2.source_code

        method_info_map = state['bug_report'].method_to_method_info

        def resolve_method_info(identifier, file_hint=None):
            method_sig = None
            local_file_hint = file_hint
            if isinstance(identifier, tuple):
                local_file_hint, method_sig = identifier
                info = method_info_map.get(identifier)
                if info:
                    return info
            else:
                method_sig = identifier

            if local_file_hint:
                info = method_info_map.get((local_file_hint, method_sig))
                if info:
                    return info

            info = method_info_map.get(method_sig)
            if info:
                return info

            for key, value in method_info_map.items():
                if isinstance(key, tuple) and key[1] == method_sig:
                    if not local_file_hint or key[0] == local_file_hint:
                        return value
            return None

        """调用链对应的源码"""
        other_call_chain = {}
        for event in related_events:
            for cal in getattr(event, "call_chain", []):
                method_info = resolve_method_info(cal)
                if method_info:
                    other_call_chain[method_info.name] = method_info

        event_method_infos = []
        for event in related_events:
            method_info = resolve_method_info((event.file, event.method))
            if method_info:
                event_method_infos.append(method_info)

        init_info = {}
        """初始化对应的源码"""
        file_source = state['source_code'].get(m1.file_path, {}) if isinstance(state['source_code'], dict) else {}
        classes = file_source.get("classes", []) if isinstance(file_source, dict) else []
        for class_info in classes:
            class_init = class_info.get('init_code') if isinstance(class_info, dict) else None
            if not class_init:
                continue
            for method_info in event_method_infos:
                if class_init.class_name == method_info.class_name:
                    init_info[method_info.class_name] = class_info
        
        # ===== 关键修改：构建详细的变量信息 =====
        variable_definitions = {}
        for var in related_vars:
            if var in state.get('variable_to_init', {}):
                variable_definitions[var] = '\n'.join(state['variable_to_init'][var][0]) if state['variable_to_init'][var] else ''
        
        # ===== 关键修改：格式化建议策略信息 =====
        formatted_suggest_policies = {}
        for var in related_vars:
            if var in suggest_polices:
                policy_info = suggest_polices[var]
                formatted_suggest_policies[var] = {
                    'strategy': policy_info.get('optimal_strategy', 'Unknown'),
                    'reason': policy_info.get('reason', 'No reason provided')
                }
        
        # ===== 关键修改：格式化相关事件信息 =====
        formatted_events = []
        for event in related_events:
            formatted_events.append({
                'variable': event.variable,
                'file': event.file,
                'method': event.method,
                'line': getattr(event, 'line', 'Unknown')
            })
        
        # 提示词
        messages = self.patch_generation_prompt.format_messages(
            file_path=m1.file_path,
            method1_name=m1.name,
            method1_code=method1_code,
            method2_name=m2.name,
            method2_code=method2_code,
            policy_input=policy_input,
            suggest_polices=formatted_suggest_policies,
            other_call_chain=other_call_chain,
            init_info=init_info,
            related_vars=list(related_vars),
            variable_definitions=variable_definitions,
            related_events=formatted_events,
        )
        
        # 产生回应 - 调用本地ollama的qwen3:14b模型
        import requests
        import json
        from langchain.schema import AIMessage

        # 将 LangChain 的消息对象转换为 Ollama 的简单消息字典结构
        def _lc_messages_to_ollama(msgs: List[Any]) -> List[Dict[str, str]]:
            simple_msgs: List[Dict[str, str]] = []
            for msg in msgs:
                role = "user"
                if isinstance(msg, SystemMessage):
                    role = "system"
                elif isinstance(msg, HumanMessage):
                    role = "user"
                elif isinstance(msg, AIMessage):
                    role = "assistant"
                # content 统一转换为字符串
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                simple_msgs.append({"role": role, "content": content})
            return simple_msgs
        
        # ===== 关键修改：改进自定义提示词 =====
        custom_prompt = f"""You are a professional Java concurrency bug repair expert.

CRITICAL INSTRUCTIONS:
1. You MUST use the EXACT code provided in Method 1 and Method 2 below
2. You MUST apply the recommended strategy for each variable
3. DO NOT generate generic examples - use the actual code provided
4. DO NOT change method signatures unless absolutely necessary
5. Follow the ChangeLog format EXACTLY as specified

TASK:
Analyze the two methods below for concurrency issues and generate repair patches.

FILE: {m1.file_path}

VARIABLES TO PROTECT:
{json.dumps(formatted_suggest_policies, indent=2, ensure_ascii=False)}

VARIABLE DEFINITIONS:
{json.dumps(variable_definitions, indent=2, ensure_ascii=False)}

RELATED EVENTS:
{json.dumps(formatted_events, indent=2, ensure_ascii=False)}

Now generate the repair patches using the EXACT code provided below:
"""
        
        # 如果 messages 是 LangChain 的消息列表，则转换为 Ollama 需要的字典格式
        if isinstance(messages, list):
            enhanced_messages = [{"role": "system", "content": custom_prompt}] + _lc_messages_to_ollama(messages)
        else:
            # 极少数情况下 format_messages 非列表，退化为单轮 user 消息
            enhanced_messages = [
                {"role": "system", "content": custom_prompt},
                {"role": "user", "content": str(messages)}
            ]
        
        # ===== 关键修改：增加调试输出 =====
        print("\n========== DEBUG: Prompt Being Sent to Ollama ==========")
        print(f"System Prompt:\n{custom_prompt}\n")
        print(f"Method 1 Name: {m1.name}")
        print(f"Method 1 Code:\n{method1_code}\n")
        print(f"Method 2 Name: {m2.name}")
        print(f"Method 2 Code:\n{method2_code}\n")
        print(f"Suggest Policies: {formatted_suggest_policies}")
        print("========================================================\n")
        
        try:
            # 构建请求数据
            payload = {
                "model": "qwen3:14b",
                "messages": enhanced_messages,
                "stream": False,
                "options": {
                    "temperature": 0.2,  # 降低温度以获得更确定性的输出
                    "top_p": 0.9,
                    "num_predict": 4096  # 增加最大生成长度
                }
            }
            
            print("正在向 Ollama 发送请求...")
            
            # 发送请求到ollama API
            ollama_response = requests.post(
                "http://localhost:11434/api/chat",
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=3000  # 5分钟超时
            )
            
            # 检查响应状态
            ollama_response.raise_for_status()
            
            # 解析响应，创建类似原来response的对象
            response_data = ollama_response.json()
            
            print("成功获取 Ollama 响应")
            
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
        
        # ===== 关键修改：增加原始响应输出 =====
        print("\n========== DEBUG: Raw Ollama Response ==========")
        print(response.content[:1000])  # 打印前1000个字符
        print("================================================\n")
        
        # 补丁解析，补丁如果冲突
        patches, policies = self._parse_patch_generation_response(
            response.content, 
            m1.name, 
            m2.name, 
            related_vars,
            formatted_suggest_policies
        )

        print("----------- Generated Patches -----------")
        import json
        print(json.dumps(patches, indent=2, ensure_ascii=False))
        print("-----------------------------------------")
        
        method_to_info = {info.name: info for info in event_method_infos}
        method_to_info.update(other_call_chain)
        method_to_info.update({m1.name: m1, m2.name: m2})
        
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
        for method, p in patches.items():
            method_info = method_to_info.get(method)
            if not method_info:
                continue
            if method_info.patch:
                # 合并p和已有的补丁并存储
                pass
            else:
                method_info.patch = p
        
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
You are a professional software development engineer who specializes in fixing bugs in concurrent programming.

CRITICAL: You must use the EXACT code provided below. Do NOT generate generic examples.

Please analyze the following two methods, identify potential concurrency issues, and generate fix patches.
Additionally, please recommend updated protection policies for related variables.

Format instructions: Each ChangeLog group must start with a description of its included fixes. The group must then list one or more pairs of (OriginalCode, FixedCode) code snippets.

Output Format:
------------
ChangeLog:1@{{file_path}}
Fix:Description: <summary of the fix>
OriginalCode{{method1_line_start}}-{{method1_line_end}}:
[{{line_num}}] <white space> <original code line>
[{{line_num}}] <white space> <original code line>
FixedCode{{method1_line_start}}-{{method1_line_end}}:
[{{line_num}}] <white space> <fixed code line>
[{{line_num}}] <white space> <fixed code line>

Repair Strategy Explanation:
<explanation of the repair strategy and why this approach was chosen>
------------

IMPORTANT: Apply the recommended strategy for each variable exactly as specified.
"""),
            HumanMessage(content="""
File Path: {file_path}

Method 1 Name: {method1_name}
Method 1 Code:
{method1_code}

Method 2 Name: {method2_name}
Method 2 Code:
{method2_code}

Variables to Protect: {related_vars}

Variable Definitions:
{variable_definitions}

Recommended Strategies (MUST FOLLOW):
{suggest_polices}

Related Concurrency Events:
{related_events}

Current Protection Policy: {policy_input}

Please generate repair patches for the EXACT code above, applying the recommended strategies.
""")
        ])

    def _create_patch_merge_prompt(self) -> ChatPromptTemplate:
        """创建用于合并补丁的提示模板"""
        return ChatPromptTemplate.from_messages([
            SystemMessage(content="""
You are a professional software development engineer who excels at merging code changes.
Please merge the following two patches and ensure the resulting code is correct and free of conflicts.

Return format:
{
    "merged_patch": "The merged code",
    "explanation": "Explanation of the merge",
    "has_conflict": false,
    "conflict_details": ""
}
"""),
            HumanMessage(content="""
Method Name: {method_name}
Original Code:
{method_code}

Existing Patch:
{existing_patch}

New Patch:
{new_patch}
""")
        ])

    def _parse_patch_generation_response(self, response: str, method1_name: str, method2_name: str, 
                                        related_vars: set, suggest_policies: Dict) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """解析LLM响应，提取补丁和策略"""
        # ===== 关键修改：改进解析逻辑 =====
        import re
        
        print("\n========== DEBUG: Parsing Response ==========")
        print(f"Response length: {len(response)}")
        print(f"First 500 chars: {response[:500]}")
        print("=============================================\n")
        
        # 尝试JSON格式解析
        try:
            import json
            # 尝试找到JSON块
            json_match = re.search(r'\{[\s\S]*"patches"[\s\S]*\}', response)
            if json_match:
                data = json.loads(json_match.group(0))
                print("成功解析JSON格式响应")
                return data.get("patches", {}), data.get("updated_policies", {})
        except Exception as e:
            print(f"JSON解析失败: {e}")
        
        # 尝试解析ChangeLog格式
        patches = {}
        policies = {}
        
        # 查找ChangeLog块
        changelog_pattern = r'ChangeLog:\d+@([^\n]+)([\s\S]*?)(?=ChangeLog:\d+@|$)'
        changelogs = re.findall(changelog_pattern, response)
        
        if changelogs:
            print(f"找到 {len(changelogs)} 个ChangeLog块")
            for file_path, content in changelogs:
                # 提取Fix描述
                fix_desc_match = re.search(r'Fix:Description:\s*([^\n]+)', content)
                fix_desc = fix_desc_match.group(1) if fix_desc_match else "Concurrency fix"
                
                # 提取Original和Fixed代码块
                original_blocks = re.findall(r'OriginalCode(\d+)-(\d+):([\s\S]*?)(?=FixedCode|\Z)', content)
                fixed_blocks = re.findall(r'FixedCode(\d+)-(\d+):([\s\S]*?)(?=OriginalCode|Repair Strategy|$)', content)
                
                # 构建补丁
                patch_content = f"ChangeLog:1@{file_path.strip()}\nFix:Description: {fix_desc}\n"
                for i, (start, end, orig_code) in enumerate(original_blocks):
                    patch_content += f"OriginalCode{start}-{end}:{orig_code}"
                    if i < len(fixed_blocks):
                        fstart, fend, fixed_code = fixed_blocks[i]
                        patch_content += f"FixedCode{fstart}-{fend}:{fixed_code}"
                
                # 根据方法名分配补丁
                if method1_name.lower() in content.lower():
                    patches[method1_name] = patch_content
                elif method2_name.lower() in content.lower():
                    patches[method2_name] = patch_content
                else:
                    # 如果无法确定，两个方法都分配
                    patches[method1_name] = patch_content
                    patches[method2_name] = patch_content
        
        # 从建议策略中提取policies
        for var in related_vars:
            if var in suggest_policies:
                policies[var] = suggest_policies[var].get('strategy', 'synchronized')
        
        # 如果没有解析到任何补丁，使用改进的回退逻辑
        if not patches:
            print("警告：无法解析补丁，使用回退逻辑")
            # ===== 关键修改：回退时也要指出问题 =====
            fallback_note = f"""
WARNING: Failed to parse LLM response. The response should contain:
1. ChangeLog format patches for {method1_name} and {method2_name}
2. Applied strategies: {suggest_policies}

Raw response (first 1000 chars):
{response[:1000]}

Please check the LLM output format.
"""
            patches = {
                method1_name: f"# Parse failed - using fallback\n{fallback_note}\n\n{response}",
                method2_name: f"# Parse failed - using fallback\n{fallback_note}\n\n{response}"
            }
        
        print(f"解析结果: {len(patches)} 个补丁, {len(policies)} 个策略")
        return patches, policies

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