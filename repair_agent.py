from typing import Dict, Any, List, Tuple, Set
from entity import ConfictMethod
from langchain_community.chat_models import ChatOpenAI
from langchain.prompts import ChatPromptTemplate
from langchain.schema import HumanMessage, SystemMessage
from initializer import Event
import json


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
        # Import 专用提示词
        self.import_patch_generation_prompt = self._create_import_patch_generation_prompt()
        self.import_patch_merge_prompt = self._create_import_patch_merge_prompt()

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
        
        # 用于跟踪已处理的方法对，避免重复
        processed_method_pairs = set()
        
        #每次迭代选取一个方法对
        for cms in confictMethods:
            # 创建唯一标识符，避免重复处理
            method_pair_id = (cms.method1.name, cms.method2.name)
            method_pair_id_2 = (cms.method2.name, cms.method1.name)
            # if(cms.method1.name == cms.method2.name):
            #     print(f"⏭️  跳过相同方法对：{method_pair_id}")
            #     continue
            
            if method_pair_id in processed_method_pairs or method_pair_id_2 in processed_method_pairs:
                print(f"⏭️  跳过已处理的方法对：{method_pair_id}")
                continue
            
            processed_method_pairs.add(method_pair_id)
            
            print(f"\n{'='*60}")
            print(f"🔧 处理方法对：{cms.method1.name} <-> {cms.method2.name}")
            print(f"{'='*60}")
            
            related_events = [event for race in state['bug_report'].method_pair_to_races[cms] for event in
                                (race.event1, race.event2)]  # 处理当前元素，返回需要添加/删除的元素
            related_vars = {event.variable for event in related_events}  # 如果属性名为 var
            suggest_polices = state['suggest_polices'] #这块可以笼统点，从cas或者voliatile、或者加锁
            policy_input = state['policies']#这块需要无歧义，详细。用结构化的格式输出。
            
            print(f"📋 相关变量：{related_vars}")
            print(f"📋 建议策略：{suggest_polices}")
            
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
            
            # ✅ 存储生成的补丁（若已存在则自动合并）
            for method_name, patch in patches.items():
                if method_name in self.patches:
                    print(f"⚠️  方法 {method_name} 已有补丁，进行合并")
                    # 尝试解析方法源码，供合并上下文使用
                    source_code = ""
                    try:
                        if hasattr(cms, 'method1') and cms.method1.name == method_name:
                            source_code = getattr(cms.method1, 'source_code', '')
                        elif hasattr(cms, 'method2') and cms.method2.name == method_name:
                            source_code = getattr(cms.method2, 'source_code', '')
                        else:
                            # 回退：从全局方法信息中按名称匹配
                            for info in state.get('bug_report', {}).method_to_method_info.values():
                                if getattr(info, 'name', None) == method_name:
                                    source_code = getattr(info, 'source_code', '')
                                    break
                    except Exception:
                        pass

                    try:
                        merged_patch = self._merge_patches(
                            existing_patch=self.patches[method_name],
                            new_patch=patch,
                            method_name=method_name,
                            source_code=source_code
                        )
                        self.patches[method_name] = merged_patch
                        print(f"✅ 合并并更新补丁：{method_name}")
                    except Exception as e:
                        print(f"⚠️  合并失败，保留原补丁：{e}")
                else:
                    self.patches[method_name] = patch
                    print(f"✅ 存储补丁：{method_name}")

            # 处理受到影响的变量
            for v, p in policies.items():
                #如果是CAS,需要修改所有的涉及到的方法
                if "redefining property" in str(p).lower():
                    affected_methods = self._find_method_pairs_affected_by(v)
                    for affected_method in affected_methods:
                        # 对affected_method进行修复处理，让其按照现有的修复策略修复代码。
                        # 如果产生的补丁有冲突，调用大模型进行补丁合并
                        pass

        print(f"\n{'='*60}")
        print(f"✅ 处理完成，共生成 {len(self.patches)} 个补丁")
        print(f"{'='*60}\n")
        
        return None

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
                var_init = state['variable_to_init'][var]
                if var_init and len(var_init) > 0:
                    variable_definitions[var] = '\n'.join(var_init[0]) if var_init[0] else ''
        
        # ===== 关键修改：格式化建议策略信息 =====
        # 对建议策略做健壮处理：允许 None、字符串或字典格式
        formatted_suggest_policies = {}
        safe_suggest_policies = suggest_polices or {}
        for var in related_vars:
            policy_info = None
            if isinstance(safe_suggest_policies, dict):
                policy_info = safe_suggest_policies.get(var)

            if isinstance(policy_info, dict):
                # 优先使用 optimal_strategy，其次使用 strategy 字段
                strategy = policy_info.get('optimal_strategy') or policy_info.get('strategy') or 'Unknown'
                reason = policy_info.get('reason', 'No reason provided')
                formatted_suggest_policies[var] = {
                    'strategy': strategy,
                    'reason': reason
                }
            elif isinstance(policy_info, str):
                # 直接给了策略字符串（如 "CAS"、"synchronized"）
                formatted_suggest_policies[var] = {
                    'strategy': policy_info,
                    'reason': 'Provided as plain string in suggest_policies'
                }
            else:
                # 缺失或不支持的类型，填充默认值，避免后续解析出错
                formatted_suggest_policies[var] = {
                    'strategy': 'Unknown',
                    'reason': 'No policy provided or unsupported policy type'
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
        
        # 产生回应 - 调用本地ollama的qwen3:30b模型
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
6. When using AtomicInteger, ALWAYS initialize it with: new AtomicInteger(0)

TASK:
Analyze the two methods below for concurrency issues and generate repair patches.

FILE: {m1.file_path}

VARIABLES TO PROTECT:
{json.dumps(formatted_suggest_policies, indent=2, ensure_ascii=False)}

VARIABLE DEFINITIONS:
{json.dumps(variable_definitions, indent=2, ensure_ascii=False)}

RELATED EVENTS:
{json.dumps(formatted_events, indent=2, ensure_ascii=False)}

IMPORTANT: Generate ONE complete ChangeLog that includes fixes for BOTH methods and the variable declaration.

OUTPUT REQUIREMENTS (MANDATORY):
- Produce EXACTLY ONE ChangeLog block and NOTHING ELSE.
- The first non-whitespace characters of your entire response MUST be: "ChangeLog:1@" (no leading markdown, no code fences, no commentary).
- The last line of your response MUST be exactly: "------------".
- DO NOT include any extra text before or after the ChangeLog block.
- DO NOT wrap the output in ``` code fences or markdown.
- If you cannot produce the required ChangeLog format, respond with EXACTLY this token and nothing else: CHANGELOG_FORMAT_ERROR

STRUCTURE EXAMPLE (FOR FORMAT ONLY; USE REAL CONTENT FROM CONTEXT):
------------
ChangeLog:1@{m1.file_path}
Fix:Description: <one-line summary of all applied fixes>
OriginalCode10-10:
[10] int x = 0;
FixedCode10-10:
[10] AtomicInteger x = new AtomicInteger(0);
Repair Strategy Explanation:
<brief reasoning (1-3 sentences)>
------------

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
        # print("\n========== DEBUG: Prompt Being Sent to Ollama ==========")
        # print(f"System Prompt:\n{custom_prompt}\n")
        # print(f"Method 1 Name: {m1.name}")
        # print(f"Method 1 Code:\n{method1_code}\n")
        # print(f"Method 2 Name: {m2.name}")
        # print(f"Method 2 Code:\n{method2_code}\n")
        # print(f"Suggest Policies: {formatted_suggest_policies}")
        # print("========================================================\n")
        
        try:
            # 构建请求数据
            payload = {
                "model": "qwen3:32b",
                "messages": enhanced_messages,
                "stream": False,
                "options": {
                    "temperature": 0.1,  # 降低温度以获得更确定性的输出
                    "top_p": 0.9,
                    "num_predict": 20000  # 增加最大生成长度
                }
            }
            
            print("正在向 Ollama 发送请求...")
            
            # 发送请求到ollama API
            ollama_response = requests.post(
                "http://localhost:11434/api/chat",
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=600  # 10分钟超时
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
        
        # ===== 增加原始响应输出 =====
        print("\n========== DEBUG: Raw Ollama Response ==========")
        print(response.content)  # 打印前1000个字符
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
        print(format_patch_dict_pretty(patches))
        print("-----------------------------------------")
        
        method_to_info = {info.name: info for info in event_method_infos}
        method_to_info.update(other_call_chain)
        method_to_info.update({m1.name: m1, m2.name: m2})

        # 对import部分的处理
        for var, p in policies.items():
            # 判断是否p是使用CAS修复策略，如果是的话，需要提取文件初始化部分，然后对初始化部分进行修复
            if p == "CAS":
                files_init = {}
                affected_files = set()  # 存储需要修改的文件
                
                # 1️⃣ 收集所有需要修改的文件及其import信息
                for method, method_info in method_to_info.items():
                    if hasattr(method_info, 'file_path') and method_info.file_path:
                        file_path = method_info.file_path
                        affected_files.add(file_path)
                        
                        # 🔧 修复：正确访问 fileInit 对象的 source_code 属性
                        if file_path in state.get('source_info', {}):
                            import_info = state['source_info'][file_path].get("imports")
                            # 检查 import_info 是否是 fileInit 对象
                            if hasattr(import_info, 'source_code'):
                                # fileInit 对象，访问其 source_code 属性
                                files_init[file_path] = import_info.source_code if import_info.source_code else []
                            elif isinstance(import_info, list):
                                # 如果是列表，直接使用
                                files_init[file_path] = import_info
                            else:
                                files_init[file_path] = []
                        else:
                            files_init[file_path] = []  # 如果没有找到，初始化为空列表
                
                # 2️⃣ 为每个受影响的文件生成import补丁
                for file_path in affected_files:
                    current_imports = files_init.get(file_path, [])
                    
                    # 🔧 修复：确保 current_imports 是可迭代的列表
                    if not isinstance(current_imports, list):
                        print(f"⚠️  警告：文件 {file_path} 的imports格式异常，跳过")
                        continue
                    
                    # 检查是否已经有AtomicInteger的import
                    has_atomic_import = any(
                        'java.util.concurrent.atomic.AtomicInteger' in str(imp) 
                        for imp in current_imports
                    )
                    
                    if not has_atomic_import:
                        # 3️⃣ 生成import补丁
                        import_patch = self._generate_import_patch(
                            file_path=file_path,
                            current_imports=current_imports,
                            required_import="java.util.concurrent.atomic.AtomicInteger",
                            variable=var
                        )
                        
                        # 4️⃣ 存储import补丁到self.patches中
                        import_patch_key = f"IMPORT@{file_path}"
                        
                        if import_patch_key in self.patches:
                            # 如果已经存在import补丁，需要合并
                            print(f"⚠️  文件 {file_path} 已有import补丁，进行合并")
                            existing_patch = self.patches[import_patch_key]
                            merged_patch = self._merge_import_patches(
                                existing_patch, 
                                import_patch,
                                file_path
                            )
                            self.patches[import_patch_key] = merged_patch
                        else:
                            self.patches[import_patch_key] = import_patch
                            print(f"✅ 为文件 {file_path} 生成import补丁")


                # ===== 修复：改进补丁分配逻辑（启用自动合并替代长度启发式） =====
                for method_name, patch_content in patches.items():
                    method_info = method_to_info.get(method_name)
                    if not method_info:
                        # 尝试通过模糊匹配找到方法
                        for key, info in method_to_info.items():
                            if method_name.lower() in key.lower() or key.lower() in method_name.lower():
                                method_info = info
                                break
                    
                    if method_info:
                        if hasattr(method_info, 'patch') and method_info.patch:
                            # 已有补丁 → 调用合并
                            print(f"🧩 合并方法级补丁：{method_name}")
                            try:
                                merged_method_patch = self._merge_patches(
                                    existing_patch=method_info.patch,
                                    new_patch=patch_content,
                                    method_name=method_name,
                                    source_code=getattr(method_info, 'source_code', '')
                                )
                                method_info.patch = merged_method_patch
                            except Exception as e:
                                print(f"⚠️ 合并失败，保留现有补丁：{e}")
                        else:
                            if hasattr(method_info, 'patch'):
                                method_info.patch = patch_content
                            print(f"✅ 为方法 {method_name} 分配了补丁")
                    else:
                        print(f"⚠️ 无法找到方法信息：{method_name}")
        
        print("\n========== Import Patches Generated ==========")
        for key, patch in self.patches.items():
            if key.startswith("IMPORT@"):
                print(f"\n文件: {key.replace('IMPORT@', '')}")
                print(patch)
        print("==============================================\n")
        
        return patches, policies

    def _merge_patches(self, existing_patch: str,
                       new_patch: str,
                       method_name: str,
                       source_code: str) -> str:
        """使用本地 Ollama LLM 合并两个方法级补丁，返回合并后的 ChangeLog 字符串。

        首选调用 LLM 合并；若失败则保守回退为保留旧补丁。
        """
        # 构建合并提示词消息
        messages = self.patch_merge_prompt.format_messages(
            method_name=method_name,
            method_code=source_code or "",
            existing_patch=existing_patch,
            new_patch=new_patch
        )

        # 将 LangChain 消息转换为 Ollama 所需格式
        def _lc_messages_to_ollama(msgs: List[Any]) -> List[Dict[str, str]]:
            from langchain.schema import AIMessage
            simple_msgs: List[Dict[str, str]] = []
            for msg in msgs:
                role = "user"
                if isinstance(msg, SystemMessage):
                    role = "system"
                elif isinstance(msg, HumanMessage):
                    role = "user"
                elif isinstance(msg, AIMessage):
                    role = "assistant"
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                simple_msgs.append({"role": role, "content": content})
            return simple_msgs

        enhanced_messages = _lc_messages_to_ollama(messages) if isinstance(messages, list) else [
            {"role": "system", "content": "You merge patches."},
            {"role": "user", "content": str(messages)}
        ]

        # 调用 Ollama 模型
        import requests
        import json as _json
        try:
            payload = {
                "model": "qwen3:32b",
                "messages": enhanced_messages,
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    "top_p": 0.9,
                    "num_predict": 4000
                }
            }
            resp = requests.post(
                "http://localhost:11434/api/chat",
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=300
            )
            resp.raise_for_status()
            data = resp.json()
            content = data.get('message', {}).get('content', '')

            # 解析合并结果
            parsed = self._parse_patch_merge_response(content)
            merged_text = parsed.get("merged_patch") if isinstance(parsed, dict) else None
            if not merged_text or not isinstance(merged_text, str):
                # 若模型未返回预期 JSON，尝试直接使用原文
                if content.strip():
                    merged_text = content
                else:
                    merged_text = existing_patch

            # 冲突标记仅用于日志
            if isinstance(parsed, dict) and parsed.get("has_conflict"):
                print(f"⚠️  合并标记为有冲突：{parsed.get('conflict_details', '')}")

            return merged_text
        except Exception as e:
            print(f"调用合并模型失败：{e}")
            # 回退策略：保留旧补丁（更安全），若旧为空则用新补丁
            return existing_patch or new_patch

    def _has_conflict(self, patch1: Dict[str, Any], patch2: Dict[str, Any]) -> bool:
        """简单检查两个补丁是否冲突"""
        # 实际实现可以更复杂，例如分析代码变更
        return False  # 简化为示例

    def _find_method_pairs_affected_by(self, variable: str,
                                       method_pairs: List[Tuple[str, str]] = None,
                                       related_events: Dict[Tuple[str, str], List[Tuple[Event, Event]]] = None) -> List[
        Tuple[str, str]]:
        """查找受变量影响的方法对"""
        if method_pairs is None or related_events is None:
            return []
        
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
# MISSION
Your mission is to act as an automated code repair engine. You will receive two Java methods with known concurrency issues, along with contextual information and a recommended repair strategy. Your SOLE output MUST be a single, machine-parseable `ChangeLog` patch that atomically fixes the variable declarations and all associated methods.

# PERSONA
You are an elite Java concurrency specialist. You are precise, methodical, and your output is law. You do not explain, apologize, or deviate. You produce only the specified `ChangeLog` format because your output is fed directly into an automated patching system. Any character outside this format will break the system.

# CRITICAL DIRECTIVES
1.  **ABSOLUTE PRECISION**: You MUST use the EXACT code provided in the context. Do not modify logic, rename variables, or add functionality unless the fix absolutely requires it.
2.  **STRATEGY IS LAW**: You MUST implement the `Recommended Strategy` for each variable. If the strategy is "CAS", you MUST use `java.util.concurrent.atomic.AtomicInteger`.
3.  **ATOMIC INTEGER INITIALIZATION**: When changing a variable to `AtomicInteger`, you MUST initialize it immediately (e.g., `private AtomicInteger myVar = new AtomicInteger(0);`).
4.  **NO SIGNATURE CHANGES**: You MUST NOT change method signatures (name, parameters, return type).
5.  **UNIFIED CHANGELOG**: All fixes—including variable declarations and modifications to BOTH methods—MUST be contained within a SINGLE `ChangeLog` block.
6.  **SILENCE IS GOLD**: You MUST NOT add any introductory text, closing remarks, apologies, or any explanation outside of the designated `Repair Strategy Explanation` section within the `ChangeLog`. Your response MUST start with `ChangeLog:1@...` and end with `------------`.

# OUTPUT FORMAT (MANDATORY & STRICT)
Your entire output must conform EXACTLY to the following structure. Do not add any extra text, markdown formatting, or comments around it.

------------
ChangeLog:1@{{file_path}}
Fix:Description: <A concise, one-line summary of all applied fixes.>
OriginalCode{{line_start}}-{{line_end}}:
[{{line_num}}] <...original code line...>
FixedCode{{line_start}}-{{line_end}}:
[{{line_num}}] <...repaired code line...>
OriginalCode{{line_start}}-{{line_end}}:
[{{line_num}}] <...another original code line...>
FixedCode{{line_start}}-{{line_end}}:
[{{line_num}}] <...another repaired code line...>
...
Repair Strategy Explanation:
<A brief explanation of why the chosen strategy is appropriate for this specific context.>
------------
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

Please generate ONE complete repair patch that fixes the variable declaration and BOTH methods above.
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

    def _create_import_patch_generation_prompt(self) -> ChatPromptTemplate:
        """创建用于生成 import 补丁的提示模板（使用严格 ChangeLog 格式）"""
        return ChatPromptTemplate.from_messages([
            SystemMessage(content="""
You are a precise Java refactoring engine specialized in managing import statements.

TASK: Generate ONE ChangeLog patch that adds the required import into the given Java file.

STRICT RULES:
1. Output EXACTLY one ChangeLog block and NOTHING ELSE.
2. First non-whitespace characters MUST be: "ChangeLog:1@".
3. End with a single line exactly: "------------".
4. Use the provided insertion line if given; otherwise place import after the last existing import, or at the top if none.
5. Do NOT modify or include unrelated lines.

FORMAT:
------------
ChangeLog:1@{file_path}
Fix:Description: Add import for <RequiredImport>
OriginalCode<start>-<end>:
[<line>] <existing line or empty>
FixedCode<start>-<end>:
[<line>] import <RequiredImport>;
Repair Strategy Explanation:
<one or two sentences max>
------------
"""),
            HumanMessage(content="""
File: {file_path}
Required Import: {required_import}
Variable Context: {variable}
Existing Imports (with line numbers):
{existing_imports}

Suggested Insertion Line: {suggested_line}
Notes: Keep ChangeLog minimal; if the exact original line is unknown or empty, leave OriginalCode body empty (just the header), and put the new import in FixedCode at the line `Suggested Insertion Line`.
""")
        ])

    def _create_import_patch_merge_prompt(self) -> ChatPromptTemplate:
        """创建用于合并 import 补丁的提示模板（输出 JSON 包裹 merged_patch）"""
        return ChatPromptTemplate.from_messages([
            SystemMessage(content="""
You are a patch merge engine focused on Java imports.
Merge the two ChangeLog patches about the same file's import section into ONE consolidated ChangeLog.

REQUIREMENTS:
- Remove duplicate imports.
- Keep only import-related edits; don't touch non-import lines.
- Keep ChangeLog strict format (single block, starts with ChangeLog:1@<file>, ends with ------------).
- It's OK to renumber lines consistently; if unknown, place imports as a contiguous block.

Return a JSON object:
{
  "merged_patch": "<the single ChangeLog block>",
  "explanation": "<brief>",
  "has_conflict": false,
  "conflict_details": ""
}
"""),
            HumanMessage(content="""
File: {file_path}
Existing Import Patch:
{existing_patch}

New Import Patch:
{new_patch}
""")
        ])

    def _parse_patch_generation_response(self, response: str, method1_name: str, method2_name: str, 
                                        related_vars: set, suggest_policies: Dict) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """解析LLM响应，提取补丁和策略"""
        import re
        
        print("\n========== DEBUG: Parsing Response ==========")
        print(f"Response length: {len(response)}")
        print(f"Last 500 chars: {response[-500:]}")
        print("=============================================\n")
        
        # 尝试JSON格式解析
        try:
            import json
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
        changelog_pattern = r'ChangeLog:\d+@([^\n]+)([\s\S]*?)(?=ChangeLog:\d+@|Repair Strategy|$)'
        changelogs = re.findall(changelog_pattern, response)
        
        if changelogs:
            print(f"找到 {len(changelogs)} 个ChangeLog块")
            
            # 合并所有 ChangeLog 块到一个补丁
            all_original_blocks = []
            all_fixed_blocks = []
            file_path = None
            fix_description = "Applied CAS strategy using AtomicInteger for thread-safe operations"
            
            for file, content in changelogs:
                if not file_path:
                    file_path = file.strip()
                
                # 提取Fix描述
                fix_desc_match = re.search(r'Fix:Description:\s*([^\n]+)', content)
                if fix_desc_match:
                    fix_description = fix_desc_match.group(1)
                
                # 提取Original和Fixed代码块
                original_blocks = re.findall(r'OriginalCode(\d+)-(\d+):([\s\S]*?)(?=FixedCode|\Z)', content)
                fixed_blocks = re.findall(r'FixedCode(\d+)-(\d+):([\s\S]*?)(?=OriginalCode|Repair Strategy|ChangeLog|$)', content)
                
                all_original_blocks.extend(original_blocks)
                all_fixed_blocks.extend(fixed_blocks)
            
            # ===== 关键修复：确保 AtomicInteger 有初始化 =====
            enhanced_fixed_blocks = []
            for start, end, fixed_code in all_fixed_blocks:
                # 检查是否包含 AtomicInteger 声明但没有初始化
                if 'AtomicInteger balance' in fixed_code and 'new AtomicInteger' not in fixed_code:
                    # 添加初始化
                    fixed_code = re.sub(
                        r'(AtomicInteger\s+balance)\s*;',
                        r'\1 = new AtomicInteger(0);',
                        fixed_code
                    )
                    print("✅ 自动添加了 AtomicInteger 初始化")
                enhanced_fixed_blocks.append((start, end, fixed_code))
            
            # 构建完整的补丁 - 为两个方法生成统一的补丁
            patch_content = f"ChangeLog:1@{file_path}\nFix:Description: {fix_description}\n"
            
            for i, (start, end, orig_code) in enumerate(all_original_blocks):
                patch_content += f"OriginalCode{start}-{end}:{orig_code}"
                if i < len(enhanced_fixed_blocks):
                    fstart, fend, fixed_code = enhanced_fixed_blocks[i]
                    patch_content += f"FixedCode{fstart}-{fend}:{fixed_code}"
            
            # ===== 关键修复：为两个方法都分配这个完整补丁 =====
            patches[method1_name] = patch_content
            patches[method2_name] = patch_content
            
            print(f"✅ 为 {method1_name} 和 {method2_name} 生成了统一的完整补丁")
        
        # 从建议策略中提取policies
        for var in related_vars:
            if var in suggest_policies:
                policies[var] = suggest_policies[var].get('strategy', 'synchronized')
        
        # 如果没有解析到任何补丁，使用回退逻辑
        if not patches:
            print("警告：无法解析补丁，使用回退逻辑")
            
            # ===== 改进的回退逻辑：尝试从响应中提取有用信息 =====
            fallback_patch = f"""# ⚠️ Automatic Parsing Failed - Manual Review Required

File: {method1_name} and {method2_name}
Recommended Strategy: {suggest_policies}

LLM Response (first 2000 chars):
{response[:2000]}

---
Please manually extract the ChangeLog from the response above.
"""
            patches = {
                method1_name: fallback_patch,
                method2_name: fallback_patch
            }
        
        print(f"解析结果: {len(patches)} 个补丁, {len(policies)} 个策略")
        return patches, policies

    def _parse_patch_merge_response(self, response: str) -> Dict[str, Any]:
        """解析LLM响应，提取合并后的补丁"""
        try:
            import json
            return json.loads(response)
        except:
            return {
                "merged_patch": f"# 合并的补丁\n{response}",
                "explanation": "合并后的补丁",
                "has_conflict": False,
                "conflict_details": ""
            }

    def _generate_import_patch(self, file_path: str, current_imports: List[str], 
                            required_import: str, variable: str) -> str:
        """
        生成 import 语句的补丁（通过本地 Ollama）。失败时回退到简单补丁。
        """
        # 计算建议插入行
        last_import_line = 0
        existing_list = []
        for imp in current_imports:
            try:
                line_num = int(imp.split(']')[0].strip('['))
                last_import_line = max(last_import_line, line_num)
                existing_list.append(imp)
            except Exception:
                existing_list.append(str(imp))
        suggested_line = last_import_line + 1 if last_import_line > 0 else 1

        # 组装提示词
        messages = self.import_patch_generation_prompt.format_messages(
            file_path=file_path,
            required_import=required_import,
            variable=variable,
            existing_imports="\n".join(existing_list) or "<none>",
            suggested_line=suggested_line,
        )

        # 转换为 Ollama 消息
        def _lc_messages_to_ollama(msgs: List[Any]) -> List[Dict[str, str]]:
            from langchain.schema import AIMessage
            simple_msgs: List[Dict[str, str]] = []
            for msg in msgs:
                role = "user"
                if isinstance(msg, SystemMessage):
                    role = "system"
                elif isinstance(msg, HumanMessage):
                    role = "user"
                elif isinstance(msg, AIMessage):
                    role = "assistant"
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                simple_msgs.append({"role": role, "content": content})
            return simple_msgs

        enhanced_messages = _lc_messages_to_ollama(messages) if isinstance(messages, list) else [
            {"role": "system", "content": "Generate import ChangeLog patch."},
            {"role": "user", "content": str(messages)}
        ]

        import requests
        try:
            payload = {
                "model": "qwen3:32b",
                "messages": enhanced_messages,
                "stream": False,
                "options": {"temperature": 0.1, "top_p": 0.9, "num_predict": 2000}
            }
            resp = requests.post(
                "http://localhost:11434/api/chat",
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=180
            )
            resp.raise_for_status()
            content = resp.json().get('message', {}).get('content', '')
            # 简单校验 ChangeLog 头
            if content.strip().startswith(f"ChangeLog:1@{file_path}"):
                return content
        except Exception as e:
            print(f"⚠️  生成 import 补丁时调用模型失败，回退：{e}")

        # 回退到简单补丁
        return (
            f"ChangeLog:1@{file_path}\n"
            f"Fix:Description: Add import for {required_import} (fallback)\n"
            f"OriginalCode{suggested_line}-{suggested_line}:\n\n"
            f"FixedCode{suggested_line}-{suggested_line}:\n"
            f"[{suggested_line}] import {required_import};\n"
            f"Repair Strategy Explanation:\nAdd required import for variable '{variable}'.\n"
            f"------------"
        )


    def _merge_import_patches(self, existing_patch: str, new_patch: str, 
                            file_path: str) -> str:
        """合并两个 import 补丁（通过本地 Ollama）。失败时回退到正则去重逻辑。"""
        messages = self.import_patch_merge_prompt.format_messages(
            file_path=file_path,
            existing_patch=existing_patch,
            new_patch=new_patch,
        )

        # 转换为 Ollama 消息
        def _lc_messages_to_ollama(msgs: List[Any]) -> List[Dict[str, str]]:
            from langchain.schema import AIMessage
            simple_msgs: List[Dict[str, str]] = []
            for msg in msgs:
                role = "user"
                if isinstance(msg, SystemMessage):
                    role = "system"
                elif isinstance(msg, HumanMessage):
                    role = "user"
                elif isinstance(msg, AIMessage):
                    role = "assistant"
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                simple_msgs.append({"role": role, "content": content})
            return simple_msgs

        enhanced_messages = _lc_messages_to_ollama(messages) if isinstance(messages, list) else [
            {"role": "system", "content": "Merge import patches."},
            {"role": "user", "content": str(messages)}
        ]

        import requests, re
        try:
            payload = {
                "model": "qwen3:32b",
                "messages": enhanced_messages,
                "stream": False,
                "options": {"temperature": 0.1, "top_p": 0.9, "num_predict": 2000}
            }
            resp = requests.post(
                "http://localhost:11434/api/chat",
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=180
            )
            resp.raise_for_status()
            content = resp.json().get('message', {}).get('content', '')
            parsed = self._parse_patch_merge_response(content)
            merged_text = parsed.get("merged_patch") if isinstance(parsed, dict) else None
            if isinstance(merged_text, str) and merged_text.strip().startswith(f"ChangeLog:1@{file_path}"):
                return merged_text
            # 若模型未返回预期 JSON/格式，尝试直接原文
            if content.strip().startswith(f"ChangeLog:1@{file_path}"):
                return content
        except Exception as e:
            print(f"⚠️  合并 import 补丁时调用模型失败，回退：{e}")

        # 回退：使用先前的正则去重合并
        existing_imports = re.findall(r'\[(\d+)\]\s*import\s+([^;]+);', existing_patch)
        new_imports = re.findall(r'\[(\d+)\]\s*import\s+([^;]+);', new_patch)
        all_imports = {}
        for line_num, import_name in existing_imports:
            all_imports[import_name.strip()] = int(line_num)
        for line_num, import_name in new_imports:
            name = import_name.strip()
            if name not in all_imports:
                max_line = max(all_imports.values()) if all_imports else 0
                all_imports[name] = max_line + 1
        sorted_imports = sorted(all_imports.items(), key=lambda x: x[1])
        fixed_code_section = "".join([f"[{ln}] import {nm};\n" for nm, ln in sorted_imports])
        start_line = sorted_imports[0][1] if sorted_imports else 1
        end_line = sorted_imports[-1][1] if sorted_imports else 1
        return (
            f"ChangeLog:1@{file_path}\n"
            f"Fix:Description: Merge required imports (fallback)\n"
            f"OriginalCode{start_line}-{end_line}:\n\n"
            f"FixedCode{start_line}-{end_line}:\n"
            f"{fixed_code_section}"
            f"Repair Strategy Explanation:\nCombine unique imports into a single block.\n"
            f"------------"
        )


class PatchConflictError(Exception):
    """补丁冲突异常"""
    pass



def format_patch_dict_pretty(data) -> str:
    """
    将片段1格式化成片段2的美观形式：
    - 冒号后直接换行
    - 字符串中的 \n 转为真实换行
    - 每一行缩进 2 个空格，保持整体 JSON 可读性
    """
    formatted_items = []

    for key, value in data.items():
        # 1. 转换字符串中的 \n 为真实换行
        value = value.replace("\\n", "\n").strip()

        # 2. 为每一行内容添加额外缩进
        indented_value = "\n".join("      " + line for line in value.splitlines())

        # 3. 构建键值对字符串
        formatted_item = f'    "{key}": \n{indented_value}'
        formatted_items.append(formatted_item)

    # 4. 拼接成完整 JSON 样式
    result = "{\n" + ",\n\n".join(formatted_items) + "\n}"
    return result
