from typing import Dict, Any, List, Tuple, Set
from entity import ConfictMethod
from langchain_community.chat_models import ChatOpenAI
from langchain.prompts import ChatPromptTemplate
from langchain.schema import HumanMessage, SystemMessage
from initializer import Event
import json
import re


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

    def _format_variable_definitions(self, variable_definitions: Dict[str, str], 
                                     related_vars: set, 
                                     state: Dict[str, Any]) -> str:
        """
        格式化变量定义为带行号的文本格式
        
        Args:
            variable_definitions: 变量定义字典
            related_vars: 相关变量集合
            state: 状态信息
            
        Returns:
            格式化后的文本，例如：
            Variable: balance
            20:     private int balance = 0;
            21:     
            Variable: count
            45:     private int count;
        """
        formatted_text = []
        
        for var in related_vars:
            formatted_text.append(f"\nVariable: {var}")
            formatted_text.append("-" * 40)
            
            # 从 variable_definitions 获取定义
            if var in variable_definitions and variable_definitions[var]:
                definition = variable_definitions[var]
                
                # 尝试从定义中提取行号和代码
                if isinstance(definition, str):
                    lines = definition.split('\n')
                    for line in lines:
                        if line.strip():
                            # 检查是否已经包含行号格式 [num]
                            if re.match(r'^\[\d+\]', line.strip()):
                                # 已有行号，转换为统一格式
                                match = re.match(r'^\[(\d+)\]\s*(.*)', line.strip())
                                if match:
                                    line_num, code = match.groups()
                                    formatted_text.append(f"{line_num}: {code}")
                                else:
                                    formatted_text.append(f"?: {line.strip()}")
                            else:
                                # 没有行号，添加占位符
                                formatted_text.append(f"?: {line.strip()}")
            else:
                # 尝试从 state 中查找变量初始化信息
                if var in state.get('variable_to_init', {}):
                    var_init = state['variable_to_init'][var]
                    if var_init and len(var_init) > 0:
                        init_lines = var_init[0] if isinstance(var_init[0], list) else [var_init[0]]
                        for idx, line in enumerate(init_lines):
                            if isinstance(line, str) and line.strip():
                                formatted_text.append(f"?: {line.strip()}")
                else:
                    formatted_text.append("?: <No definition found>")
            
            formatted_text.append("")  # 空行分隔
        
        return "\n".join(formatted_text)

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
        
        # 格式化变量定义为带行号的文本
        formatted_variable_definitions = self._format_variable_definitions(
            variable_definitions, 
            related_vars, 
            state
        )
        
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
            variable_definitions=formatted_variable_definitions,
            related_events=formatted_events,
        )
        
        # ===== 调试输出：打印 HumanMessage 中的变量值 =====
        print("\n" + "="*80)
        print("🔍 [DEBUG] HumanMessage 中的变量值")
        print("="*80)
        print(f"method1_name: {m1.name}")
        print(f"method2_name: {m2.name}")
        print(f"related_vars: {related_vars}")
        print(f"variable_definitions (formatted):\n{formatted_variable_definitions}")
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
        print(json.dumps(patches, indent=2, ensure_ascii=False))
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
        """创建用于生成补丁的提示模板 - 要求JSON输出"""
        return ChatPromptTemplate.from_messages([
            SystemMessage(content="""
**MISSION**

You are an automated Java concurrency bug repair engine. Receive Java methods with concurrency issues, context, and repair strategy. Output JSON-formatted patches.

**ROLE**

Elite Java concurrency specialist. Precise, concise, JSON-only output. Your output feeds directly into an automated patching system.

**CORE RULES**

1. Use exact code from context. Never modify logic or rename variables unless fix requires it
2. Analyze protection strategy for variables in given code and adopt appropriate strategy
3. Recommended strategy must be followed unless it prevents successful repair
4. Infer from previous initialization code whether current patch needs initialization
5. Do not change method signatures unless absolutely necessary
6. Response MUST be valid JSON format. No markdown blocks, no commentary outside JSON

**OUTPUT FORMAT (MANDATORY)**

This is an example, you don't need to output the content directly, but your output should follow this structure strictly:
```json
{
  "patches": {
    "method_name_1": {
      "file_path": "path/to/file.java",
      "description": "Brief description of fix",
      "changes": [
        {
          "original_start_line": 20,
          "original_end_line": 22,
          "original_code": [
            {"line": 20, "code": "    private int balance = 0;"},
            {"line": 21, "code": "    "},
            {"line": 22, "code": "    public void withdraw(int amount) {"}
          ],
          "fixed_start_line": 20,
          "fixed_end_line": 23,
          "fixed_code": [
            {"line": 20, "code": "    private AtomicInteger balance = new AtomicInteger(0);"},
            {"line": 21, "code": "    "},
            {"line": 22, "code": "    public void withdraw(int amount) {"},
            {"line": 23, "code": "        balance.addAndGet(-amount);"}
          ]
        }
      ]
    },
    "method_name_2": {
      "file_path": "path/to/file.java",
      "description": "Brief description of fix",
      "changes": [...]
    }
  },
  "policies": {
    "variable_name": "CAS/synchronized",
    "another_variable": "CAS/synchronized"
  },
  "explanation": "1-3 sentences explaining the overall repair strategy"
}
```

**FAILURE RESPONSE**

If cannot generate valid JSON, output: `{"error": "JSON_FORMAT_ERROR", "details": "reason"}`

"""),
            HumanMessage(content="""
File Path: {file_path}

Method 1 Name: {method1_name}
Method 1 Code: {method1_code}

Method 2 Name: {method2_name}
Method 2 Code: {method2_code}

Variables to Protect: {related_vars}

Variable Definitions (with line numbers):
{variable_definitions}

Recommended Strategies (MUST FOLLOW):
{suggest_polices}

Related Concurrency Events:
{related_events}

Current Protection Policy: {policy_input}

Now generate the repair patches in JSON format. Include fixes for BOTH methods and variable declarations.
""")
        ])

    def _create_patch_merge_prompt(self) -> ChatPromptTemplate:
        """创建用于合并补丁的提示模板"""
        return ChatPromptTemplate.from_messages([
            SystemMessage(content="""
You are a professional software development engineer who excels at merging code changes.
Please merge the following two patches and ensure the resulting code is correct and free of conflicts.

Return format (valid JSON):
{
    "merged_patch": "The merged code or JSON patch structure",
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

TASK: Generate a JSON-formatted import patch.

STRICT RULES:
1. Output MUST be valid JSON.
2. Add required import at appropriate location.
3. Do NOT modify or include unrelated lines.

FORMAT:
{
  "file_path": "path/to/file.java",
  "description": "Add import for <RequiredImport>",
  "changes": [
    {
      "original_start_line": 10,
      "original_end_line": 10,
      "original_code": [
        {"line": 10, "code": ""}
      ],
      "fixed_start_line": 10,
      "fixed_end_line": 10,
      "fixed_code": [
        {"line": 10, "code": "import java.util.concurrent.atomic.AtomicInteger;"}
      ]
    }
  ],
  "explanation": "Added required import for thread-safe operations"
}
"""),
            HumanMessage(content="""
File: {file_path}
Required Import: {required_import}
Variable Context: {variable}
Existing Imports (with line numbers):
{existing_imports}

Suggested Insertion Line: {suggested_line}
""")
        ])

    def _create_import_patch_merge_prompt(self) -> ChatPromptTemplate:
        """创建用于合并 import 补丁的提示模板"""
        return ChatPromptTemplate.from_messages([
            SystemMessage(content="""
You are a patch merge engine focused on Java imports.
Merge the two import patches into ONE consolidated JSON patch.

REQUIREMENTS:
- Remove duplicate imports.
- Keep only import-related edits.
- Output valid JSON format.

Return a JSON object:
{
  "merged_patch": "<JSON patch structure or text>",
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
        """解析LLM响应，提取JSON格式的补丁和策略"""
        
        print("\n" + "="*80)
        print("🔍 [DEBUG] 开始解析 LLM 响应")
        print("="*80)
        print(f"响应长度: {len(response)} 字符")
        print(f"Method 1: {method1_name}")
        print(f"Method 2: {method2_name}")
        print(f"相关变量: {related_vars}")
        print("="*80 + "\n")
        
        # 尝试解析JSON格式
        try:
            # 提取JSON块（处理markdown包裹）
            json_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', response)
            if json_match:
                json_str = json_match.group(1)
            else:
                # 尝试直接解析整个响应
                json_str = response.strip()
            
            data = json.loads(json_str)
            
            if "error" in data:
                print(f"⚠️  LLM返回错误: {data.get('details', 'Unknown error')}")
                raise ValueError("LLM返回错误标记")
            
            patches = data.get("patches", {})
            policies = data.get("policies", {})
            
            # 转换JSON patch格式为字符串格式（保持向后兼容）
            string_patches = {}
            for method_name, patch_data in patches.items():
                string_patches[method_name] = json.dumps(patch_data, indent=2, ensure_ascii=False)
            
            print(f"✅ 成功解析JSON格式响应: {len(string_patches)} 个补丁, {len(policies)} 个策略")
            return string_patches, policies
            
        except (json.JSONDecodeError, ValueError) as e:
            print(f"⚠️  JSON解析失败: {e}")
            print("⚠️  尝试回退到文本解析...")
        
        # 回退逻辑：使用建议策略
        patches = {}
        policies = {}
        
        for var in related_vars:
            if var in suggest_policies:
                policies[var] = suggest_policies[var].get('strategy', 'synchronized')
        
        # 生成回退补丁
        fallback_patch_json = {
            "file_path": "unknown",
            "description": "Automatic parsing failed - manual review required",
            "changes": [{
                "original_start_line": 0,
                "original_end_line": 0,
                "original_code": [],
                "fixed_start_line": 0,
                "fixed_end_line": 0,
                "fixed_code": [],
                "note": f"LLM Response (first 2000 chars): {response[:2000]}"
            }]
        }
        
        fallback_patch_str = json.dumps(fallback_patch_json, indent=2, ensure_ascii=False)
        patches[method1_name] = fallback_patch_str
        patches[method2_name] = fallback_patch_str
        
        print(f"⚠️  使用回退逻辑: {len(patches)} 个补丁, {len(policies)} 个策略\n")
        return patches, policies

    def _parse_patch_merge_response(self, response: str) -> Dict[str, Any]:
        """解析LLM响应，提取合并后的补丁"""
        try:
            # 提取JSON块
            json_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', response)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_str = response.strip()
            
            result = json.loads(json_str)
            print("✅ 成功解析JSON格式的合并响应")
            return result
        except:
            print("⚠️  JSON解析失败，使用默认格式")
            return {
                "merged_patch": response,
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
            {"role": "system", "content": "Generate import patch in JSON format."},
            {"role": "user", "content": str(messages)}
        ]

        print("\n" + "="*80)
        print("📦 [DEBUG] IMPORT PATCH GENERATION - LLM REQUEST")
        print("="*80)
        print(f"📝 目标文件: {file_path}")
        print(f"📝 需要导入: {required_import}")
        print(f"📝 相关变量: {variable}")
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
            
            print("\n" + "="*80)
            print("📥 [DEBUG] IMPORT PATCH GENERATION - LLM RESPONSE")
            print("="*80)
            print("✅ Ollama 返回的原始内容")
            print("="*80 + "\n")
            
            # 尝试解析JSON
            try:
                json_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', content)
                if json_match:
                    json_data = json.loads(json_match.group(1))
                else:
                    json_data = json.loads(content.strip())
                
                # 转换为字符串格式
                return json.dumps(json_data, indent=2, ensure_ascii=False)
            except:
                print("⚠️  JSON解析失败，使用回退逻辑")
        except Exception as e:
            print(f"\n❌ [ERROR] 生成 import 补丁时调用模型失败: {e}\n")

        # 回退到JSON格式补丁
        fallback_patch = {
            "file_path": file_path,
            "description": f"Add import for {required_import} (fallback)",
            "changes": [{
                "original_start_line": suggested_line,
                "original_end_line": suggested_line,
                "original_code": [{"line": suggested_line, "code": ""}],
                "fixed_start_line": suggested_line,
                "fixed_end_line": suggested_line,
                "fixed_code": [{"line": suggested_line, "code": f"import {required_import};"}]
            }],
            "explanation": f"Add required import for variable '{variable}'"
        }
        print("⚠️  使用回退JSON补丁")
        return json.dumps(fallback_patch, indent=2, ensure_ascii=False)

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
            {"role": "system", "content": "Merge import patches in JSON format."},
            {"role": "user", "content": str(messages)}
        ]

        print("\n" + "="*80)
        print("🔀 [DEBUG] IMPORT PATCH MERGE - LLM REQUEST")
        print("="*80)
        print(f"📝 目标文件: {file_path}")
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
            
            print("\n" + "="*80)
            print("📥 [DEBUG] IMPORT PATCH MERGE - LLM RESPONSE")
            print("="*80)
            print("✅ Ollama 返回的原始内容")
            print("="*80 + "\n")
            
            parsed = self._parse_patch_merge_response(content)
            merged_text = parsed.get("merged_patch") if isinstance(parsed, dict) else content
            
            return merged_text
        except Exception as e:
            print(f"\n❌ [ERROR] 合并 import 补丁时调用模型失败: {e}\n")

        # 回退：使用正则去重合并
        print("⚠️  使用JSON回退合并逻辑")
        try:
            existing_data = json.loads(existing_patch)
            new_data = json.loads(new_patch)
            
            # 合并imports
            all_imports = {}
            for change in existing_data.get("changes", []):
                for code_item in change.get("fixed_code", []):
                    code = code_item.get("code", "")
                    if "import" in code:
                        all_imports[code.strip()] = code_item.get("line", 0)
            
            for change in new_data.get("changes", []):
                for code_item in change.get("fixed_code", []):
                    code = code_item.get("code", "")
                    if "import" in code and code.strip() not in all_imports:
                        max_line = max(all_imports.values()) if all_imports else 0
                        all_imports[code.strip()] = max_line + 1
            
            sorted_imports = sorted(all_imports.items(), key=lambda x: x[1])
            fixed_code = [{"line": ln, "code": code} for code, ln in sorted_imports]
            
            merged_patch = {
                "file_path": file_path,
                "description": "Merge required imports (fallback)",
                "changes": [{
                    "original_start_line": sorted_imports[0][1] if sorted_imports else 1,
                    "original_end_line": sorted_imports[-1][1] if sorted_imports else 1,
                    "original_code": [],
                    "fixed_start_line": sorted_imports[0][1] if sorted_imports else 1,
                    "fixed_end_line": sorted_imports[-1][1] if sorted_imports else 1,
                    "fixed_code": fixed_code
                }],
                "explanation": "Combined unique imports into a single block"
            }
            return json.dumps(merged_patch, indent=2, ensure_ascii=False)
        except:
            # 最终回退
            return existing_patch


class PatchConflictError(Exception):
    """补丁冲突异常"""
    pass