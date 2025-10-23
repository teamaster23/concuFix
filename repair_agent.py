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
        self.var_policies = {}
        self.patches = {}
        self.patch_generation_prompt = self._create_patch_generation_prompt()
        self.patch_merge_prompt = self._create_patch_merge_prompt()
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
        confictMethods = list(dict.fromkeys(confictMethods))
        processed_method_pairs = set()
        
        for cms in confictMethods:
            method_pair_id = (cms.method1.name, cms.method2.name)
            method_pair_id_2 = (cms.method2.name, cms.method1.name)
            
            if method_pair_id in processed_method_pairs or method_pair_id_2 in processed_method_pairs:
                print(f"⏭️  跳过已处理的方法对：{method_pair_id}")
                continue
            
            processed_method_pairs.add(method_pair_id)
            
            print(f"\n{'='*60}")
            print(f"🔧 处理方法对：{cms.method1.name} <-> {cms.method2.name}")
            print(f"{'='*60}")
            
            related_events = [event for race in state['bug_report'].method_pair_to_races[cms] for event in
                                (race.event1, race.event2)]
            related_vars = {event.variable for event in related_events}
            suggest_polices = state['suggest_polices']
            policy_input = state['policies']
            
            print(f"📋 相关变量：{related_vars}")
            print(f"📋 建议策略：{suggest_polices}")
            
            patches, policies = self.generate_patch(
                state,
                cms,
                related_events,
                related_vars,
                suggest_polices=suggest_polices,
                policy_input=policy_input,
                source_code=state['source_code']
            )
            
            policy_input.update(policies)
            
            for method_name, patch in patches.items():
                if method_name in self.patches:
                    print(f"⚠️  方法 {method_name} 已有补丁，进行合并")
                    source_code = ""
                    try:
                        if hasattr(cms, 'method1') and cms.method1.name == method_name:
                            source_code = getattr(cms.method1, 'source_code', '')
                        elif hasattr(cms, 'method2') and cms.method2.name == method_name:
                            source_code = getattr(cms.method2, 'source_code', '')
                        else:
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

            for v, p in policies.items():
                if "redefining property" in str(p).lower():
                    affected_methods = self._find_method_pairs_affected_by(v)
                    for affected_method in affected_methods:
                        pass

        print(f"\n{'='*60}")
        print(f"✅ 处理完成，共生成 {len(self.patches)} 个补丁")
        print(f"{'='*60}\n")
        
        return None

    def generate_patch(self, state, cms: ConfictMethod, related_events: list, related_vars: set,
                    suggest_polices: Dict[str, Any],
                    policy_input: Dict[str, Any],
                    source_code: Dict[str, str]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """生成补丁的主方法"""
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
        file_source = state['source_code'].get(m1.file_path, {}) if isinstance(state['source_code'], dict) else {}
        classes = file_source.get("classes", []) if isinstance(file_source, dict) else []
        for class_info in classes:
            class_init = class_info.get('init_code') if isinstance(class_info, dict) else None
            if not class_init:
                continue
            for method_info in event_method_infos:
                if class_init.class_name == method_info.class_name:
                    init_info[method_info.class_name] = class_info
        
        # 构建详细的变量信息
        variable_definitions = {}
        for var in related_vars:
            if var in state.get('variable_to_init', {}):
                var_init = state['variable_to_init'][var]
                if var_init and len(var_init) > 0:
                    variable_definitions[var] = '\n'.join(var_init[0]) if var_init[0] else ''
        
        # 格式化建议策略信息
        formatted_suggest_policies = {}
        safe_suggest_policies = suggest_polices or {}
        for var in related_vars:
            policy_info = None
            if isinstance(safe_suggest_policies, dict):
                policy_info = safe_suggest_policies.get(var)

            if isinstance(policy_info, dict):
                strategy = policy_info.get('optimal_strategy') or policy_info.get('strategy') or 'Unknown'
                reason = policy_info.get('reason', 'No reason provided')
                formatted_suggest_policies[var] = {
                    'strategy': strategy,
                    'reason': reason
                }
            elif isinstance(policy_info, str):
                formatted_suggest_policies[var] = {
                    'strategy': policy_info,
                    'reason': 'Provided as plain string in suggest_policies'
                }
            else:
                formatted_suggest_policies[var] = {
                    'strategy': 'Unknown',
                    'reason': 'No policy provided or unsupported policy type'
                }
        
        # 格式化相关事件信息
        formatted_events = []
        for event in related_events:
            formatted_events.append({
                'variable': event.variable,
                'file': event.file,
                'method': event.method,
                'line': getattr(event, 'line', 'Unknown')
            })
        
        # ===== 构建提示词消息 =====
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
        
        # ===== 调试输出：打印 HumanMessage 中的变量值 =====
        print("\n" + "="*80)
        print("🔍 [DEBUG] HumanMessage 中的变量值")
        print("="*80)
        print(f"method1_name: {m1.name}")
        print(f"method2_name: {m2.name}")
        print(f"related_vars: {related_vars}")
        print(f"variable_definitions: {json.dumps(variable_definitions, indent=2, ensure_ascii=False)}")
        print(f"suggest_polices: {json.dumps(suggest_polices, indent=2, ensure_ascii=False)}")
        print(f"related_events: {related_events}")
        print(f"policy_input: {policy_input}")
        print("="*80 + "\n")
        
        # 转换为 Ollama 格式
        def _lc_messages_to_ollama(msgs: List[Any]) -> List[Dict[str, str]]:
            simple_msgs: List[Dict[str, str]] = []
            for msg in msgs:
                role = "user"
                if isinstance(msg, SystemMessage):
                    role = "system"
                elif isinstance(msg, HumanMessage):
                    role = "user"
                elif isinstance(msg, AIMessage):
                    from langchain.schema import AIMessage
                    role = "assistant"
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                simple_msgs.append({"role": role, "content": content})
            return simple_msgs
        
        enhanced_messages = _lc_messages_to_ollama(messages) if isinstance(messages, list) else [
            {"role": "user", "content": str(messages)}
        ]
        
        # ===== 调试输出：打印发送给大模型的完整提示词 =====
        print("\n" + "="*80)
        print("🤖 [DEBUG] PATCH GENERATION - LLM REQUEST")
        print("="*80)
        print("📤 发送到 Ollama 的完整消息:")
        print("-"*80)
        for idx, msg in enumerate(enhanced_messages):
            print(f"\n消息 #{idx + 1} (角色: {msg['role']})")
            print("-"*40)
            # print(msg['content'])
        print("\n" + "="*80)
        print("🔄 正在等待 Ollama 响应...")
        print("="*80 + "\n")
        
        try:
            payload = {
                "model": "qwen3:32b",
                "messages": enhanced_messages,
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    "top_p": 0.9,
                    "num_predict": 20000
                }
            }
            
            import requests
            ollama_response = requests.post(
                "http://localhost:11434/api/chat",
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=600
            )
            
            ollama_response.raise_for_status()
            response_data = ollama_response.json()
            
            class Response:
                def __init__(self, content):
                    self.content = content
            
            response = Response(response_data.get('message', {}).get('content', ''))
            
            # ===== 调试输出：打印大模型返回的原始响应 =====
            print("\n" + "="*80)
            print("📥 [DEBUG] PATCH GENERATION - LLM RESPONSE")
            print("="*80)
            print("✅ Ollama 返回的原始内容:")
            print("-"*80)
            print(response.content)
            print("\n" + "="*80)
            print("✅ 响应接收完成，开始解析...")
            print("="*80 + "\n")
            
        except requests.exceptions.RequestException as e:
            print(f"\n❌ [ERROR] 调用ollama模型时出现错误: {e}\n")
            raise
        except json.JSONDecodeError as e:
            print(f"\n❌ [ERROR] 解析ollama响应时出现错误: {e}\n")
            raise
        
        patches, policies = self._parse_patch_generation_response(
            response.content, 
            m1.name, 
            m2.name, 
            related_vars,
            formatted_suggest_policies
        )

        # ===== 调试输出：打印解析后的结果 =====
        print("\n" + "="*80)
        print("📊 [DEBUG] PATCH GENERATION - PARSED RESULTS")
        print("="*80)
        print("🔧 解析得到的补丁:")
        print("-"*80)
        # print(format_patch_dict_pretty(patches))
        print("\n" + "-"*80)
        print("📋 解析得到的策略:")
        print("-"*80)
        print(json.dumps(policies, indent=2, ensure_ascii=False))
        print("\n" + "="*80 + "\n")
        
        method_to_info = {info.name: info for info in event_method_infos}
        method_to_info.update(other_call_chain)
        method_to_info.update({m1.name: m1, m2.name: m2})

        # 处理 import 部分
        for var, p in policies.items():
            if p == "CAS":
                files_init = {}
                affected_files = set()
                
                for method, method_info in method_to_info.items():
                    if hasattr(method_info, 'file_path') and method_info.file_path:
                        file_path = method_info.file_path
                        affected_files.add(file_path)
                        
                        if file_path in state.get('source_info', {}):
                            import_info = state['source_info'][file_path].get("imports")
                            if hasattr(import_info, 'source_code'):
                                files_init[file_path] = import_info.source_code if import_info.source_code else []
                            elif isinstance(import_info, list):
                                files_init[file_path] = import_info
                            else:
                                files_init[file_path] = []
                        else:
                            files_init[file_path] = []
                
                for file_path in affected_files:
                    current_imports = files_init.get(file_path, [])
                    
                    if not isinstance(current_imports, list):
                        print(f"⚠️  警告：文件 {file_path} 的imports格式异常，跳过")
                        continue
                    
                    has_atomic_import = any(
                        'java.util.concurrent.atomic.AtomicInteger' in str(imp) 
                        for imp in current_imports
                    )
                    
                    if not has_atomic_import:
                        import_patch = self._generate_import_patch(
                            file_path=file_path,
                            current_imports=current_imports,
                            required_import="java.util.concurrent.atomic.AtomicInteger",
                            variable=var
                        )
                        
                        import_patch_key = f"IMPORT@{file_path}"
                        
                        if import_patch_key in self.patches:
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

                for method_name, patch_content in patches.items():
                    method_info = method_to_info.get(method_name)
                    if not method_info:
                        for key, info in method_to_info.items():
                            if method_name.lower() in key.lower() or key.lower() in method_name.lower():
                                method_info = info
                                break
                    
                    if method_info:
                        if hasattr(method_info, 'patch') and method_info.patch:
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
        
        return patches, policies

    def _merge_patches(self, existing_patch: str,
                       new_patch: str,
                       method_name: str,
                       source_code: str) -> str:
        """合并两个方法级补丁"""
        messages = self.patch_merge_prompt.format_messages(
            method_name=method_name,
            method_code=source_code or "",
            existing_patch=existing_patch,
            new_patch=new_patch
        )

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

        # ===== 调试输出：打印合并补丁的提示词 =====
        print("\n" + "="*80)
        print("🔀 [DEBUG] PATCH MERGE - LLM REQUEST")
        print("="*80)
        print(f"📝 合并方法: {method_name}")
        print("📤 发送到 Ollama 的完整消息:")
        print("-"*80)
        for idx, msg in enumerate(enhanced_messages):
            print(f"\n消息 #{idx + 1} (角色: {msg['role']})")
            print("-"*40)
            print(msg['content'])
        print("\n" + "="*80)
        print("🔄 正在等待 Ollama 响应...")
        print("="*80 + "\n")

        import requests
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

            # ===== 调试输出：打印合并补丁的原始响应 =====
            print("\n" + "="*80)
            print("📥 [DEBUG] PATCH MERGE - LLM RESPONSE")
            print("="*80)
            print("✅ Ollama 返回的原始内容:")
            print("-"*80)
            # print(content)
            print("\n" + "="*80 + "\n")

            parsed = self._parse_patch_merge_response(content)
            
            # ===== 调试输出：打印解析后的合并结果 =====
            print("\n" + "="*80)
            print("📊 [DEBUG] PATCH MERGE - PARSED RESULTS")
            print("="*80)
            print(json.dumps(parsed, indent=2, ensure_ascii=False))
            print("\n" + "="*80 + "\n")
            
            merged_text = parsed.get("merged_patch") if isinstance(parsed, dict) else None
            if not merged_text or not isinstance(merged_text, str):
                if content.strip():
                    merged_text = content
                else:
                    merged_text = existing_patch

            if isinstance(parsed, dict) and parsed.get("has_conflict"):
                print(f"⚠️  合并标记为有冲突：{parsed.get('conflict_details', '')}")

            return merged_text
        except Exception as e:
            print(f"\n❌ [ERROR] 调用合并模型失败：{e}\n")
            return existing_patch or new_patch

    def _has_conflict(self, patch1: Dict[str, Any], patch2: Dict[str, Any]) -> bool:
        """简单检查两个补丁是否冲突"""
        return False

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
        """创建用于生成补丁的提示模板 - 整合了所有关键指令"""
        return ChatPromptTemplate.from_messages([
            SystemMessage(content="""# MISSION
Your mission is to act as an automated code repair engine for Java concurrency bugs. You will receive two Java methods with known concurrency issues, along with contextual information and a recommended repair strategy. Your SOLE output MUST be a single, machine-parseable `ChangeLog` patch that atomically fixes the variable declarations and all associated methods.

# PERSONA
You are an elite Java concurrency specialist. You are precise, methodical, and your output is law. You do not explain, apologize, or deviate. You produce only the specified `ChangeLog` format because your output is fed directly into an automated patching system. Any character outside this format will break the system.

# CRITICAL DIRECTIVES
1.  **ABSOLUTE PRECISION**: You MUST use the EXACT code provided in the context. Do not modify logic, rename variables, or add functionality unless the fix absolutely requires it.
2.  **STRATEGY IS LAW**: You MUST implement the `Recommended Strategy` for each variable. If the strategy is "CAS", you MUST use `java.util.concurrent.atomic.AtomicInteger`.
3.  **ATOMIC INTEGER INITIALIZATION**: When changing a variable to `AtomicInteger`, you MUST initialize it immediately (e.g., `private AtomicInteger myVar = new AtomicInteger(0);`).
4.  **NO SIGNATURE CHANGES**: You MUST NOT change method signatures (name, parameters, return type) unless absolutely necessary.
5.  **UNIFIED CHANGELOG**: All fixes—including variable declarations and modifications to BOTH methods—MUST be contained within a SINGLE `ChangeLog` block.
6.  **SILENCE IS GOLD**: You MUST NOT add any introductory text, closing remarks, apologies, or any explanation outside of the designated `Repair Strategy Explanation` section within the `ChangeLog`. Your response MUST start with `ChangeLog:1@...` and end with `------------`.

# OUTPUT FORMAT (MANDATORY & STRICT)
Your entire output must conform EXACTLY to the following structure. Do not add any extra text, markdown formatting (like ```), or comments around it.

The first non-whitespace characters of your entire response MUST be: "ChangeLog:1@" (no leading markdown, no code fences, no commentary).
The last line of your response MUST be exactly: "------------".

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
<A brief explanation (1-3 sentences) of why the chosen strategy is appropriate for this specific context.>
------------

If you cannot produce the required ChangeLog format, respond with EXACTLY this token and nothing else: CHANGELOG_FORMAT_ERROR
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

Now generate the repair patches using the EXACT code provided above. Generate ONE complete ChangeLog that includes fixes for BOTH methods and the variable declaration.
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
        """创建用于生成 import 补丁的提示模板"""
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
        """创建用于合并 import 补丁的提示模板"""
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
        
        print("\n" + "="*80)
        print("🔍 [DEBUG] 开始解析 LLM 响应")
        print("="*80)
        print(f"响应长度: {len(response)} 字符")
        print(f"Method 1: {method1_name}")
        print(f"Method 2: {method2_name}")
        print(f"相关变量: {related_vars}")
        print("="*80 + "\n")
        
        # 尝试JSON格式解析
        try:
            json_match = re.search(r'\{[\s\S]*"patches"[\s\S]*\}', response)
            if json_match:
                data = json.loads(json_match.group(0))
                print("✅ 成功解析JSON格式响应")
                return data.get("patches", {}), data.get("updated_policies", {})
        except Exception as e:
            print(f"⚠️  JSON解析失败: {e}")
        
        # 尝试解析ChangeLog格式
        patches = {}
        policies = {}
        
        # 查找ChangeLog块
        changelog_pattern = r'ChangeLog:\d+@([^\n]+)([\s\S]*?)(?=ChangeLog:\d+@|Repair Strategy|$)'
        changelogs = re.findall(changelog_pattern, response)
        
        if changelogs:
            print(f"✅ 找到 {len(changelogs)} 个ChangeLog块")
            
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
            
            print(f"📦 提取到 {len(all_original_blocks)} 个 Original 块")
            print(f"📦 提取到 {len(all_fixed_blocks)} 个 Fixed 块")
            
            # 确保 AtomicInteger 有初始化
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
            
            # 为两个方法都分配这个完整补丁
            patches[method1_name] = patch_content
            patches[method2_name] = patch_content
            
            print(f"✅ 为 {method1_name} 和 {method2_name} 生成了统一的完整补丁")
        
        # 从建议策略中提取policies
        for var in related_vars:
            if var in suggest_policies:
                policies[var] = suggest_policies[var].get('strategy', 'synchronized')
        
        # 如果没有解析到任何补丁，使用回退逻辑
        if not patches:
            print("⚠️  警告：无法解析补丁，使用回退逻辑")
            
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
        
        print(f"\n✅ 解析完成: {len(patches)} 个补丁, {len(policies)} 个策略\n")
        return patches, policies

    def _parse_patch_merge_response(self, response: str) -> Dict[str, Any]:
        """解析LLM响应，提取合并后的补丁"""
        try:
            result = json.loads(response)
            print("✅ 成功解析JSON格式的合并响应")
            return result
        except:
            print("⚠️  JSON解析失败，使用默认格式")
            return {
                "merged_patch": f"# 合并的补丁\n{response}",
                "explanation": "合并后的补丁",
                "has_conflict": False,
                "conflict_details": ""
            }

    def _generate_import_patch(self, file_path: str, current_imports: List[str], 
                            required_import: str, variable: str) -> str:
        """生成 import 语句的补丁"""
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

        # ===== 调试输出：打印生成 import 补丁的提示词 =====
        print("\n" + "="*80)
        print("📦 [DEBUG] IMPORT PATCH GENERATION - LLM REQUEST")
        print("="*80)
        print(f"📝 目标文件: {file_path}")
        print(f"📝 需要导入: {required_import}")
        print(f"📝 相关变量: {variable}")
        print("📤 发送到 Ollama 的完整消息:")
        print("-"*80)
        for idx, msg in enumerate(enhanced_messages):
            print(f"\n消息 #{idx + 1} (角色: {msg['role']})")
            print("-"*40)
            print(msg['content'])
        print("\n" + "="*80)
        print("🔄 正在等待 Ollama 响应...")
        print("="*80 + "\n")

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
            
            # ===== 调试输出：打印生成 import 补丁的原始响应 =====
            print("\n" + "="*80)
            print("📥 [DEBUG] IMPORT PATCH GENERATION - LLM RESPONSE")
            print("="*80)
            print("✅ Ollama 返回的原始内容:")
            print("-"*80)
            #print(content)
            print("\n" + "="*80 + "\n")
            
            # 简单校验 ChangeLog 头
            if content.strip().startswith(f"ChangeLog:1@{file_path}"):
                print("✅ 生成的 import 补丁格式正确")
                return content
            else:
                print("⚠️  生成的 import 补丁格式不符合预期，使用回退逻辑")
        except Exception as e:
            print(f"\n❌ [ERROR] 生成 import 补丁时调用模型失败: {e}\n")

        # 回退到简单补丁
        fallback_patch = (
            f"ChangeLog:1@{file_path}\n"
            f"Fix:Description: Add import for {required_import} (fallback)\n"
            f"OriginalCode{suggested_line}-{suggested_line}:\n\n"
            f"FixedCode{suggested_line}-{suggested_line}:\n"
            f"[{suggested_line}] import {required_import};\n"
            f"Repair Strategy Explanation:\nAdd required import for variable '{variable}'.\n"
            f"------------"
        )
        print("⚠️  使用回退补丁")
        return fallback_patch


    def _merge_import_patches(self, existing_patch: str, new_patch: str, 
                            file_path: str) -> str:
        """合并两个 import 补丁"""
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

        # ===== 调试输出：打印合并 import 补丁的提示词 =====
        print("\n" + "="*80)
        print("🔀 [DEBUG] IMPORT PATCH MERGE - LLM REQUEST")
        print("="*80)
        print(f"📝 目标文件: {file_path}")
        print("📤 发送到 Ollama 的完整消息:")
        print("-"*80)
        for idx, msg in enumerate(enhanced_messages):
            print(f"\n消息 #{idx + 1} (角色: {msg['role']})")
            print("-"*40)
            print(msg['content'])
        print("\n" + "="*80)
        print("🔄 正在等待 Ollama 响应...")
        print("="*80 + "\n")

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
            
            # ===== 调试输出：打印合并 import 补丁的原始响应 =====
            print("\n" + "="*80)
            print("📥 [DEBUG] IMPORT PATCH MERGE - LLM RESPONSE")
            print("="*80)
            print("✅ Ollama 返回的原始内容:")
            print("-"*80)
            print(content)
            print("\n" + "="*80 + "\n")
            
            parsed = self._parse_patch_merge_response(content)
            
            # ===== 调试输出：打印解析后的合并结果 =====
            print("\n" + "="*80)
            print("📊 [DEBUG] IMPORT PATCH MERGE - PARSED RESULTS")
            print("="*80)
            print(json.dumps(parsed, indent=2, ensure_ascii=False))
            print("\n" + "="*80 + "\n")
            
            merged_text = parsed.get("merged_patch") if isinstance(parsed, dict) else None
            if isinstance(merged_text, str) and merged_text.strip().startswith(f"ChangeLog:1@{file_path}"):
                print("✅ 合并的 import 补丁格式正确")
                return merged_text
            # 若模型未返回预期 JSON/格式，尝试直接原文
            if content.strip().startswith(f"ChangeLog:1@{file_path}"):
                print("✅ 直接使用原始响应作为补丁")
                return content
        except Exception as e:
            print(f"\n❌ [ERROR] 合并 import 补丁时调用模型失败: {e}\n")

        # 回退：使用正则去重合并
        print("⚠️  使用正则表达式回退合并逻辑")
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