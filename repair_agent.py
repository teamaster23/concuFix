from typing import Dict, Any, List, Tuple, Set, Optional
from entity import ConfictMethod
from initializer import Event
import requests
import json
import re


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
                    suggest_polices: Dict[str, Any], policy_input: Dict[str, Any],
                    source_code: Dict[str, str]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """方法体对应的源码"""
        m1 = cms.method1
        m2 = cms.method2
        method1_name = m1.name
        method2_name = m2.name
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
        """相关事件对应的方法信息"""
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
                'file': event.file,
                'method': event.method,
                'line': getattr(event, 'line', 'Unknown'),
            })

        # 对formatted_events去重，将相同file和method的事件合并，行号全部集中至line中并去重
        unique_events = []
        # 以 (file, method) 为键聚合事件行号
        event_map: Dict[Tuple[str, str], Set[str]] = {}
        for ev in formatted_events:
            file_key = str(ev.get('file', ''))
            method_key = str(ev.get('method', ''))
            line_val = ev.get('line', 'Unknown')
            # 统一为字符串，便于序列化与去重
            if isinstance(line_val, list):
                line_list = [str(x) for x in line_val]
            else:
                line_list = [str(line_val)]

            key = (file_key, method_key)
            if key not in event_map:
                event_map[key] = set()
            for lv in line_list:
                if lv:  # 过滤空字符串
                    event_map[key].add(lv)

        # 将聚合后的结果转为列表；对数字行号按数值排序，其它保留字典序
        def sort_key(x: str):
            try:
                return (0, int(x))
            except ValueError:
                return (1, x)

        for (f, m), lines in event_map.items():
            sorted_lines = sorted(lines, key=sort_key)
            unique_events.append({
                'file': f,
                'method': m,
                # 将所有相关行号合并到同一字段中（保持字段名为 line 以兼容现有结构）
                'line': sorted_lines
            })

        # 去重后的事件列表
        formatted_events = unique_events

        # ===== 精简：仅构造一个字符串 custom_prompt 并直接发送 =====
        import json
        import re
        # print(json.dumps(source_code, indent=2, ensure_ascii=False, default=str))

        # 统一将上下文与格式要求合并为单一字符串提示
        custom_prompt = f"""
**ROLE**
You are an automated Java concurrency bug repair engine that outputs structured JSON data.

**MISSION**
Analyze the provided Java methods with concurrency issues and generate repair patches in strict JSON format.

**INPUT CONTEXT**

## Method Information
- **Method 1**: {method1_name}
  - File: {m1.file_path}
  - Source Code:
{json.dumps(method1_code, ensure_ascii=False, indent=2)}

- **Method 2**: {method2_name}
  - File: {m2.file_path}
  - Source Code:
{json.dumps(method2_code, ensure_ascii=False, indent=2)}

## Variables Requiring Protection
{json.dumps(list(related_vars), ensure_ascii=False, indent=2)}

## Variable Definitions
{json.dumps(variable_definitions, ensure_ascii=False, indent=2)}

## Applied Protection Policies 
These strategies have been confirmed and MUST be strictly followed:
{json.dumps(policy_input, ensure_ascii=False, indent=2)}

## Recommended Strategies (Reference Only)
{json.dumps(formatted_suggest_policies, ensure_ascii=False, indent=2)}

## Concurrency Events
{json.dumps(unique_events, ensure_ascii=False, indent=2)}

## Call chain Information
{json.dumps(other_call_chain, ensure_ascii=False, indent=2)}

## Initialization Information
{json.dumps(init_info, ensure_ascii=False, indent=2, default=str)}

---

**REPAIR RULES**

1. **Code Accuracy**: 
    Use EXACT code from the provided context. Only modify what is necessary for thread-safety.

2. **Strategy Selection**:
   - **MUST** follow strategies in "Applied Protection Policies" if present
   - **MAY** adjust "Recommended Strategies" if they conflict with correctness
   
3. **CAS Strategy Requirements**:
   - Replace primitive types with AtomicXXX (e.g., `int` → `AtomicInteger`)
   - Initialize properly (e.g., `private AtomicInteger balance = new AtomicInteger(0);`)
   - Update all access patterns (e.g., `balance++` → `balance.incrementAndGet()`)
   
4. **Synchronized Strategy Requirements**:
   - Choose appropriate lock object:
     * Instance field → use `this` or dedicated lock
     * Static field → use `ClassName.class`
     * External object field → lock on that object

5. **Method Signatures**: Keep unchanged unless absolutely necessary

---

**OUTPUT FORMAT (MANDATORY)**

You MUST output a single valid JSON object with this exact structure:

{{
  "changelogs": [
    {{
      "id": 1,
      "file_path": "path/to/File.java",
      "description": "Brief description of the fix",
      "changes": [
        {{
          "original_code": {{
            "start_line": <number>,
            "end_line": <number>,
            "lines": [
              {{"line_num": <number>, "content": "original code line"}}
            ]
          }},
          "fixed_code": {{
            "start_line": <number>,
            "end_line": <number>,
            "lines": [
              {{"line_num": <number>, "content": "fixed code line"}}
            ]
          }}
        }}
      ]
    }}
  ],
  "applied_strategies": {{
    "variable_name": {{
      "target_variable": "variable_name",
      "optimal_strategy": "CAS"
    }}
  }},
  "repair_explanation": "1-3 sentences explaining the overall repair strategy and why it ensures thread-safety."
}}

**Field Specifications**:

- `changelogs`: Array of changelog objects, one per file modified
  - `id`: Sequential number (1, 2, 3, ...)
  - `file_path`: Full path to the Java file
  - `description`: Concise summary of what was fixed
  - `changes`: Array of code change objects
    - `original_code.lines`: Array of original code lines with line numbers
    - `fixed_code.lines`: Array of fixed code lines with line numbers
    - Line numbers in `fixed_code` should match `original_code` when replacing existing lines
    - If adding new lines, use sequential numbering

- `applied_strategies`: Dictionary mapping variable names to their protection strategies
  - `target_variable`: The exact variable name being protected
  - `optimal_strategy`: One of: `"CAS"`, `"synchronized"`, `"volatile"`
  - **DO NOT** include a `reason` field

- `repair_explanation`: Brief justification (max 3 sentences)

---

**CRITICAL REQUIREMENTS**

1. Output MUST be valid, parseable JSON
2. Do NOT wrap JSON in markdown code blocks
3. Do NOT add any text before or after the JSON object
4. First character MUST be opening brace, last character MUST be closing brace
5. All string values must be properly escaped
6. Use double quotes for JSON keys and string values

**FAILURE RESPONSE**

If you cannot generate a valid repair, output exactly:
{{
  "error": "Unable to generate repair",
  "reason": "Brief explanation of why repair failed"
}}

---

**NOW GENERATE THE REPAIR**

Analyze the methods `{method1_name}` and `{method2_name}`, apply the appropriate concurrency protection strategies, and output the repair in the JSON format specified above.
"""


        print("==============发送给ollama的原文==============")
        print(custom_prompt)
        print("============================================\n")

        try:
            payload = {
                "model": "qwen3:32b",
                "messages": [
                    {"role": "user", "content": custom_prompt}
                ],
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    "top_p": 0.9,
                    "num_predict": 8000
                }
            }

            print("正在向 Ollama 发送请求...")

            ollama_response = requests.post(
                "http://localhost:11434/api/chat",
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=600
            )

            ollama_response.raise_for_status()

            response_data = ollama_response.json()

            print("成功获取 Ollama 响应")

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
        print(response.content)  
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

        采用单字符串提示进行合并；失败则保守回退为保留旧补丁。
        """
        import requests
        # 构造单字符串提示
        custom_prompt = (
            "You are a professional software development engineer who excels at merging code changes.\n"
            "Please merge the following two patches and ensure the resulting code is correct and free of conflicts.\n\n"
            "Return format (JSON):\n"
            "{\n"
            "    \"merged_patch\": \"The merged code\",\n"
            "    \"explanation\": \"Explanation of the merge\",\n"
            "    \"has_conflict\": false,\n"
            "    \"conflict_details\": \"\"\n"
            "}\n\n"
            f"Method Name: {method_name}\n"
            f"Original Code:\n{source_code or ''}\n\n"
            f"Existing Patch:\n{existing_patch}\n\n"
            f"New Patch:\n{new_patch}\n"
        )

        try:
            payload = {
                "model": "qwen3:32b",
                "messages": [{"role": "user", "content": custom_prompt}],
                "stream": False,
                "options": {"temperature": 0.1, "top_p": 0.9, "num_predict": 4000}
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


    def _parse_patch_generation_response(self, response: str, method1_name: str, method2_name: str, 
                                        related_vars: set, suggest_policies: Dict) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """解析LLM的JSON响应，提取补丁和策略"""
        import re
        import json
        
        print("\n========== DEBUG: Parsing JSON Response ==========")
        print(f"Response length: {len(response)}")
        print(f"First 200 chars: {response[:200]}")
        print("==================================================\n")
        
        # 辅助函数：提取JSON部分
        def extract_json(text: str) -> Optional[str]:
            """从响应中提取JSON对象"""
            # 移除可能的markdown代码块标记
            text = re.sub(r'```json\s*', '', text)
            text = re.sub(r'```\s*', '', text)
            text = text.strip()
            
            # 查找JSON对象的边界
            start = text.find('{')
            end = text.rfind('}')
            
            if start != -1 and end != -1 and start < end:
                return text[start:end + 1]
            return None
        
        try:
            # 1. 提取JSON字符串
            json_str = extract_json(response)
            if not json_str:
                raise ValueError("无法在响应中找到JSON对象")
            
            # 2. 解析JSON
            data = json.loads(json_str)
            
            # 3. 验证必需字段
            if "changelogs" not in data:
                raise ValueError("JSON中缺少 'changelogs' 字段")
            
            # 4. 转换为旧格式的patches字典（保持兼容性）
            patches = {}
            changelogs = data.get("changelogs", [])
            
            for changelog in changelogs:
                file_path = changelog.get("file_path", "")
                description = changelog.get("description", "")
                changes = changelog.get("changes", [])
                
                # 构建ChangeLog格式字符串（用于向后兼容）
                patch_content = f"ChangeLog:{changelog.get('id', 1)}@{file_path}\n"
                patch_content += f"Fix:Description: {description}\n"
                
                for change in changes:
                    orig = change.get("original_code", {})
                    fixed = change.get("fixed_code", {})
                    
                    # Original code section
                    orig_start = orig.get("start_line", 0)
                    orig_end = orig.get("end_line", 0)
                    patch_content += f"OriginalCode{orig_start}-{orig_end}:\n"
                    for line_obj in orig.get("lines", []):
                        patch_content += f"[{line_obj.get('line_num')}] {line_obj.get('content')}\n"
                    
                    # Fixed code section
                    fixed_start = fixed.get("start_line", 0)
                    fixed_end = fixed.get("end_line", 0)
                    patch_content += f"FixedCode{fixed_start}-{fixed_end}:\n"
                    for line_obj in fixed.get("lines", []):
                        patch_content += f"[{line_obj.get('line_num')}] {line_obj.get('content')}\n"
                
                # 为两个方法都分配补丁
                patches[method1_name] = patch_content
                patches[method2_name] = patch_content
            
            # 5. 提取策略（已经是正确的格式，不包含reason）
            policies = {}
            applied_strategies = data.get("applied_strategies", {})
            
            for var_name, strategy_obj in applied_strategies.items():
                # 只提取 optimal_strategy 字段
                policies[var_name] = strategy_obj.get("optimal_strategy", "synchronized")
            
            print(f"✅ 成功解析JSON: {len(patches)} 个补丁, {len(policies)} 个策略")
            print(f"策略详情: {json.dumps(policies, ensure_ascii=False, indent=2)}")
            
            return patches, policies
            
        except json.JSONDecodeError as e:
            print(f"❌ JSON解析失败: {e}")
            print(f"尝试解析的内容: {json_str[:500] if json_str else 'None'}")
            
        except Exception as e:
            print(f"❌ 解析过程出错: {e}")
        
        # 回退逻辑：生成错误提示补丁
        print("⚠️  使用回退逻辑生成补丁")
        fallback_patch = f"""# ⚠️ JSON Parsing Failed - Manual Review Required

    LLM Response (first 1000 chars):
    {response[:1000]}

    ---
    Expected JSON format but received invalid response.
    Please review the raw output above and manually create the patch.
    """
        
        patches = {
            method1_name: fallback_patch,
            method2_name: fallback_patch
        }
        
        # 从建议策略中提取默认策略
        policies = {}
        for var in related_vars:
            if var in suggest_policies:
                strategy_info = suggest_policies[var]
                if isinstance(strategy_info, dict):
                    policies[var] = strategy_info.get('optimal_strategy') or strategy_info.get('strategy', 'synchronized')
                else:
                    policies[var] = str(strategy_info)
        
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

        # 构造单字符串提示
        custom_prompt = (
            "You are a precise Java refactoring engine specialized in managing import statements.\n\n"
            "TASK: Generate ONE ChangeLog patch that adds the required import into the given Java file.\n\n"
            "STRICT RULES:\n"
            "1. Output EXACTLY one ChangeLog block and NOTHING ELSE.\n"
            "2. First non-whitespace characters MUST be: \"ChangeLog:1@\".\n"
            "3. End with a single line exactly: \"------------\".\n"
            "4. Use the provided insertion line if given; otherwise place import after the last existing import, or at the top if none.\n"
            "5. Do NOT modify or include unrelated lines.\n\n"
            "FORMAT EXAMPLE (format only):\n"
            "------------\n"
            f"ChangeLog:1@{file_path}\n"
            "Fix:Description: Add import for <RequiredImport>\n"
            "OriginalCode<start>-<end>:\n"
            "[<line>] <existing line or empty>\n"
            "FixedCode<start>-<end>:\n"
            "[<line>] import <RequiredImport>;\n"
            "Repair Strategy Explanation:\n"
            "<one or two sentences max>\n"
            "------------\n\n"
            f"File: {file_path}\n"
            f"Required Import: {required_import}\n"
            f"Variable Context: {variable}\n"
            "Existing Imports (with line numbers):\n"
            f"{('\n').join(existing_list) or '<none>'}\n\n"
            f"Suggested Insertion Line: {suggested_line}\n"
            "Notes: Keep ChangeLog minimal; if the exact original line is unknown or empty, leave OriginalCode body empty (just the header), and put the new import in FixedCode at the line `Suggested Insertion Line`.\n"
        )

        import requests
        try:
            payload = {
                "model": "qwen3:32b",
                "messages": [{"role": "user", "content": custom_prompt}],
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
        # 构造单字符串提示
        custom_prompt = (
            "You are a patch merge engine focused on Java imports.\n"
            "Merge the two ChangeLog patches about the same file's import section into ONE consolidated ChangeLog.\n\n"
            "REQUIREMENTS:\n"
            "- Remove duplicate imports.\n"
            "- Keep only import-related edits; don't touch non-import lines.\n"
            "- Keep ChangeLog strict format (single block, starts with ChangeLog:1@<file>, ends with ------------).\n"
            "- It's OK to renumber lines consistently; if unknown, place imports as a contiguous block.\n\n"
            "Return a JSON object:\n"
            "{\n"
            "  \"merged_patch\": \"<the single ChangeLog block>\",\n"
            "  \"explanation\": \"<brief>\",\n"
            "  \"has_conflict\": false,\n"
            "  \"conflict_details\": \"\"\n"
            "}\n\n"
            f"File: {file_path}\n\n"
            f"Existing Import Patch:\n{existing_patch}\n\n"
            f"New Import Patch:\n{new_patch}\n"
        )

        import requests, re
        try:
            payload = {
                "model": "qwen3:32b",
                "messages": [{"role": "user", "content": custom_prompt}],
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