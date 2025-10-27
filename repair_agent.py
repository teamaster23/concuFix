from typing import Dict, Any, List, Tuple, Set, Optional
from entity import ConfictMethod
from initializer import Event
import requests
import json
import re


class RepairAgent():
    """修复智能体节点，整合补丁生成逻辑"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.var_policies = {}  # Variable → Policy
        self.patches = {}  # Method → Patch
        self.fixed_methods = {}  # 存储已成功修复的方法 Method Name → Fixed Code

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
            
            # 尝试最多5次生成和验证补丁
            success = False
            error_history = []
            current_patch = None
            
            for attempt in range(5):
                print(f"\n🔄 尝试第 {attempt + 1} 次生成补丁...")
                
                # 生成补丁
                patches, policies = self.generate_patch(
                    state,
                    cms,
                    related_events,
                    related_vars,
                    suggest_polices=suggest_polices,
                    policy_input=policy_input,
                    source_code=state['source_code'],
                    error_history=error_history,
                    previous_patch=current_patch
                )
                
                current_patch = patches
                
                # 验证补丁
                is_valid, error_msg = self.exam_for_patch(patches, cms, state)
                
                if is_valid:
                    print(f"✅ 补丁验证成功！")
                    success = True
                    
                    # 更新策略
                    policy_input.update(policies)
                    
                    # 存储成功的补丁和修复后的方法
                    for method_name, patch in patches.items():
                        self.patches[method_name] = patch
                        # 从补丁中提取修复后的代码并存储
                        fixed_code = self._extract_fixed_code(patch)
                        if fixed_code:
                            self.fixed_methods[method_name] = fixed_code
                        print(f"✅ 存储补丁：{method_name}")
                    
                    break
                else:
                    print(f"❌ 补丁验证失败：{error_msg}")
                    error_history.append({
                        'attempt': attempt + 1,
                        'patch': current_patch,
                        'error': error_msg
                    })
            
            if not success:
                print(f"❌ 方法对 {method_pair_id} 在5次尝试后仍无法生成正确补丁，跳过")

        print(f"\n{'='*60}")
        print(f"✅ 处理完成，共生成 {len(self.patches)} 个补丁")
        print(f"补丁内容：{format_patch_dict_pretty(self.patches)}")
        print(f"{'='*60}\n")
        
        # 所有方法对修复完成后，判断需要添加的import
        if self.fixed_methods:
            print(f"\n{'='*60}")
            print(f"🔍 检查需要添加的import语句...")
            print(f"{'='*60}\n")
            self._determine_required_imports(state)
        
        return None

    def _extract_fixed_code(self, patch_content: str) -> str:
        """从补丁中提取修复后的代码"""
        try:
            # 尝试中文标记
            if "修复后代码:" in patch_content:
                parts = patch_content.split("修复后代码:")
                if len(parts) > 1:
                    return parts[1].strip()
            
            # 尝试英文标记
            if "Fixed Code:" in patch_content:
                parts = patch_content.split("Fixed Code:")
                if len(parts) > 1:
                    return parts[1].strip()
            
            return ""
        except Exception as e:
            print(f"⚠️  提取修复代码时出错: {e}")
            return ""

    def exam_for_patch(self, patches: Dict[str, Any], cms: ConfictMethod, 
                      state: Dict[str, Any]) -> Tuple[bool, str]:
        """
        检测补丁是否成功修改
        返回 (是否成功, 错误信息)
        """
        try:
            # 基本验证：检查补丁是否为空
            if not patches:
                return False, "补丁为空"
            
            # 检查每个方法的补丁
            for method_name, patch_content in patches.items():
                if not patch_content or len(patch_content.strip()) == 0:
                    return False, f"方法 {method_name} 的补丁内容为空"
                
                # 检查是否包含修复后代码
                
                if "修复后代码:" not in patch_content and "Fixed Code:" not in patch_content:
                    return False, f"方法 {method_name} 的补丁缺少修复后代码部分"
                
                # 检查修复后的代码是否包含必要的同步机制
                fixed_code = self._extract_fixed_code(patch_content)
                if not fixed_code:
                    return False, f"方法 {method_name} 无法提取修复后的代码"
                
                # 根据策略验证修复代码
                for var, policy in state.get('policies', {}).items():
                    if policy == "CAS":
                        if "Atomic" not in fixed_code:
                            return False, f"方法 {method_name} 应使用CAS策略但未找到Atomic相关代码"
                    elif policy == "synchronized":
                        if "synchronized" not in fixed_code:
                            return False, f"方法 {method_name} 应使用synchronized但未找到相关代码"
                    elif policy == "volatile":
                        if "volatile" not in fixed_code:
                            return False, f"方法 {method_name} 应使用volatile但未找到相关代码"
            
            return True, ""
            
        except Exception as e:
            return False, f"验证过程出错: {str(e)}"

    def generate_patch(self, state, cms: ConfictMethod, related_events: list, related_vars: set,
                    suggest_polices: Dict[str, Any], policy_input: Dict[str, Any],
                    source_code: Dict[str, str], error_history: List[Dict] = None,
                    previous_patch: Dict[str, Any] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """生成补丁"""
        m1 = cms.method1
        m2 = cms.method2
        method1_name = m1.name
        method2_name = m2.name
        # 如果方法已经被修复过，使用最新的修复版本
        method1_code = self.fixed_methods.get(method1_name, m1.source_code)
        method2_code = self.fixed_methods.get(method2_name, m2.source_code)

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
        
        variable_definitions = {}
        for var in related_vars:
            if var in state.get('variable_to_init', {}):
                var_init = state['variable_to_init'][var]
                if var_init and len(var_init) > 0:
                    variable_definitions[var] = '\n'.join(var_init[0]) if var_init[0] else ''
        
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
        
        formatted_events = []
        for event in related_events:
            formatted_events.append({
                'file': event.file,
                'method': event.method,
                'line': getattr(event, 'line', 'Unknown'),
            })

        unique_events = []
        event_map: Dict[Tuple[str, str], Set[str]] = {}
        for ev in formatted_events:
            file_key = str(ev.get('file', ''))
            method_key = str(ev.get('method', ''))
            line_val = ev.get('line', 'Unknown')
            if isinstance(line_val, list):
                line_list = [str(x) for x in line_val]
            else:
                line_list = [str(line_val)]

            key = (file_key, method_key)
            if key not in event_map:
                event_map[key] = set()
            for lv in line_list:
                if lv:
                    event_map[key].add(lv)

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
                'line': sorted_lines
            })

        formatted_events = unique_events
        
        # 构建错误历史信息（如果有的话）
        error_context = ""
        if error_history:
            error_context = "\n## Previous Attempts and Errors\n"
            for err in error_history:
                error_context += f"\n### Attempt {err['attempt']}\n"
                error_context += f"Error: {err['error']}\n"
                error_context += f"Previous Patch:\n{json.dumps(err['patch'], ensure_ascii=False, indent=2)}\n"
            error_context += "\nPlease analyze the errors above and generate a corrected patch.\n"

        import json

        custom_prompt = f"""
**ROLE**
You are an automated Java concurrency bug repair engine that outputs structured JSON data.

**MISSION**
Analyze the provided Java methods with concurrency issues and generate repair patches in the new format: Buggy Method + Fixed Code.

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
{related_vars}

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

{error_context}

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

**OUTPUT FORMAT (MANDATORY) - NEW FORMAT**

You MUST output a single valid JSON object with this exact structure:

{{
  "patches": {{
    "method_name": {{
      "fixed_code": "complete fixed method code here"
    }}
  }},
  "applied_strategies": {{
    "variable_name": {{
      "target_variable": "variable_name",
      "optimal_strategy": "strategy_name"
    }}
  }},
  "repair_explanation": "1-3 sentences explaining the overall repair strategy and why it ensures thread-safety."
}}

**Note**:

1. You may use <think></think> tags for reasoning, but you must limit thinking to maximum 2000 tokens.
2. After the <think></think> tags,you MUST produce the json of Output. Output MUST follow the exact JSON structure specified above.
3. Output MUST be valid, parseable JSON. Do NOT add any text before or after the JSON object
4. The `fixed_code` should contain the number of the lines as in the fixed method.
5. The `optimal_strategy` must be one of: "CAS", "synchronized".
---

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
        
        # print("\n========== DEBUG: Raw Ollama Response ==========")
        # print(response.content)  
        # print("================================================\n")
        
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
        
        return patches, policies

    def _determine_required_imports(self, state: Dict[str, Any]):
        """
        所有方法对修复完成后，判断需要添加什么import
        """
        if not self.fixed_methods:
            print("没有成功修复的方法，跳过import检查")
            return
        
        # 按文件分组整理修复后的方法
        file_to_methods = {}
        for method_name, fixed_code in self.fixed_methods.items():
            # 从state中找到对应的文件路径
            file_path = None
            for info in state.get('bug_report', {}).method_to_method_info.values():
                if getattr(info, 'name', None) == method_name:
                    file_path = getattr(info, 'file_path', None)
                    break
            
            if file_path:
                if file_path not in file_to_methods:
                    file_to_methods[file_path] = []
                file_to_methods[file_path].append({
                    'method_name': method_name,
                    'fixed_code': fixed_code
                })
        
        # 对每个文件判断需要的import
        for file_path, methods in file_to_methods.items():
            print(f"\n📁 检查文件: {file_path}")
            
            # 获取当前文件的imports
            current_imports = []
            if file_path in state.get('source_info', {}):
                import_info = state['source_info'][file_path].get("imports")
                if hasattr(import_info, 'source_code'):
                    current_imports = import_info.source_code if import_info.source_code else []
                elif isinstance(import_info, list):
                    current_imports = import_info
            
            # 构建所有修复后的方法代码
            all_fixed_code = "\n\n".join([m['fixed_code'] for m in methods])
            
            # 调用LLM判断需要的import
            required_imports = self._ask_llm_for_imports(
                file_path=file_path,
                fixed_methods_code=all_fixed_code,
                current_imports=current_imports,
                method_names=[m['method_name'] for m in methods]
            )
            
            if required_imports:
                print(f"✅ 需要添加以下import:")
                for imp in required_imports:
                    print(f"   - {imp}")
                
                # 存储import信息
                import_patch_key = f"IMPORT@{file_path}"
                self.patches[import_patch_key] = {
                    'file_path': file_path,
                    'imports': required_imports
                }
            else:
                print(f"✅ 无需添加新的import")

    def _ask_llm_for_imports(self, file_path: str, fixed_methods_code: str, 
                            current_imports: List[str], method_names: List[str]) -> List[str]:
        """
        询问LLM需要添加什么import语句
        """
        custom_prompt = f"""
**ROLE**
You are a Java import management expert.

**MISSION**
Analyze the fixed Java methods and determine what import statements are needed.

**INPUT CONTEXT**

## File Path
{file_path}

## Method Names
{json.dumps(method_names, ensure_ascii=False, indent=2)}

## Fixed Methods Code
```java
{fixed_methods_code}
```

## Current Imports
{json.dumps(current_imports, ensure_ascii=False, indent=2)}

---

**ANALYSIS RULES**

1. Check if the fixed code uses any classes that require imports
2. Common cases:
   - AtomicInteger, AtomicLong, AtomicBoolean → java.util.concurrent.atomic.*
   - ReentrantLock → java.util.concurrent.locks.ReentrantLock
   - Collections utilities → java.util.*
3. Do NOT suggest imports that are already present in current imports
4. Do NOT suggest imports for classes in the same package
5. Do NOT suggest imports for java.lang.* (automatically imported)

---

**OUTPUT FORMAT (MANDATORY)**

You MUST output a single valid JSON object:

{{
  "required_imports": [
    "java.util.concurrent.atomic.AtomicInteger",
    "java.util.concurrent.locks.ReentrantLock"
  ],
  "explanation": "Brief explanation of why these imports are needed"
}}

If no new imports are needed, return:
{{
  "required_imports": [],
  "explanation": "All required classes are already imported or in java.lang"
}}

**CRITICAL REQUIREMENTS**

1. Output MUST be valid, parseable JSON
2. Do NOT wrap JSON in markdown code blocks
3. Do NOT add any text before or after the JSON object
4. required_imports MUST be an array of strings (can be empty)

---

**NOW ANALYZE THE IMPORTS**
"""

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
                    "num_predict": 2000
                }
            }

            print(f"🔍 正在分析文件 {file_path} 的import需求...")

            ollama_response = requests.post(
                "http://localhost:11434/api/chat",
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=300
            )

            ollama_response.raise_for_status()
            response_data = ollama_response.json()
            content = response_data.get('message', {}).get('content', '')
            
            # 解析JSON响应
            json_str = self._extract_json(content)
            if json_str:
                data = json.loads(json_str)
                required_imports = data.get('required_imports', [])
                explanation = data.get('explanation', '')
                
                if explanation:
                    print(f"   说明: {explanation}")
                
                return required_imports
            else:
                print(f"⚠️  无法解析LLM响应")
                return []
                
        except Exception as e:
            print(f"⚠️  分析import时出错: {e}")
            return []

    def _extract_json(self, text: str) -> Optional[str]:
        """从响应中提取JSON对象"""
        text = re.sub(r'```json\s*', '', text)
        text = re.sub(r'```\s*', '', text)
        text = text.strip()
        
        start = text.find('{')
        end = text.rfind('}')
        
        if start != -1 and end != -1 and start < end:
            return text[start:end + 1]
        return None

    def _parse_patch_generation_response(self, response: str, method1_name: str, method2_name: str, 
                                        related_vars: set, suggest_policies: Dict) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """解析LLM的JSON响应，提取补丁和策略（新格式：修复后代码）"""
        import re
        import json
        
        print("\n========== DEBUG: Parsing JSON Response ==========")
        print(f"Response length: {len(response)}")
        print(f"Complete response: {response}")
        print("==================================================\n")

        # 此处将response中的<think></think>标签内容在json中删除
        response = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL)
        print("清理后的响应内容:")
        print(response)
        print("=================================")

        try:
            json_str = self._extract_json(response)
            if not json_str:
                raise ValueError("无法在响应中找到JSON对象")
            
            data = json.loads(json_str)
            
            if "patches" not in data:
                raise ValueError("JSON中缺少 'patches' 字段")
            
            # 转换为新格式的patches字典
            patches = {}
            patches_data = data.get("patches", {})
            
            for method_name, patch_info in patches_data.items():
                fixed_code = patch_info.get("fixed_code", "")
                
                # 构建新格式的补丁字符串
                patch_content = f"修复后代码:\n{json.dumps(fixed_code, ensure_ascii=False, indent=2)}"
                patches[method_name] = patch_content
            
            # 如果没有为两个方法都生成补丁，使用相同的补丁
            if method1_name not in patches and patches:
                patches[method1_name] = list(patches.values())[0]
            if method2_name not in patches and patches:
                patches[method2_name] = list(patches.values())[0]
            
            # 提取策略
            policies = {}
            applied_strategies = data.get("applied_strategies", {})
            
            for var_name, strategy_obj in applied_strategies.items():
                policies[var_name] = strategy_obj.get("optimal_strategy", "synchronized")
            
            print(f"✅ 成功解析JSON: {len(patches)} 个补丁, {len(policies)} 个策略")
            print(f"策略详情: {json.dumps(policies, ensure_ascii=False, indent=2)}")
            
            return patches, policies
            
        except json.JSONDecodeError as e:
            print(f"❌ JSON解析失败: {e}")
            print(f"尝试解析的内容: {json_str[:500] if json_str else 'None'}")
            
        except Exception as e:
            print(f"❌ 解析过程出错: {e}")
        
        # 回退逻辑
        print("⚠️  使用回退逻辑生成补丁")
        fallback_patch = f"""
        错误方法:
        # ⚠️ JSON Parsing Failed - Manual Review Required
         
        修复后代码:
        LLM Response (last 1000 chars):
        {response[1000:]}
        
        ---
        Expected JSON format but received invalid response.
        Please review the raw output above and manually create the patch.
        """ 
        
        patches = {
            method1_name: fallback_patch,
            method2_name: fallback_patch
        }
        
        policies = {}
        for var in related_vars:
            if var in suggest_policies:
                strategy_info = suggest_policies[var]
                if isinstance(strategy_info, dict):
                    policies[var] = strategy_info.get('optimal_strategy') or strategy_info.get('strategy', 'synchronized')
                else:
                    policies[var] = str(strategy_info)
        
        return patches, policies


class PatchConflictError(Exception):
    """补丁冲突异常"""
    pass


def format_patch_dict_pretty(data) -> str:
    """格式化补丁字典输出"""
    formatted_items = []

    for key, value in data.items():
        value = value.replace("\\n", "\n").strip()
        indented_value = "\n".join("      " + line for line in value.splitlines())
        formatted_item = f'    "{key}": \n{indented_value}'
        formatted_items.append(formatted_item)

    result = "{\n" + ",\n\n".join(formatted_items) + "\n}" 
    return result