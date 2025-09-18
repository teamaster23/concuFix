import os
import re
from collections import defaultdict
from typing import Dict, List, Tuple, Set
from dataclasses import dataclass
from pathlib import Path
from pathlib import Path
from typing import List, Dict
from entity import classInit, fileInit
from tree_sitter import Language, Parser
import tree_sitter_java  # 官方预编译 Java 绑定
from typing import Tuple  # Python 3.9+ 可以直接用内置 tuple 替代

JAVA_LANGUAGE = Language(tree_sitter_java.language())
parser = Parser(JAVA_LANGUAGE)


@dataclass
class SourceLine:
    line_num: int
    content: str

    def __str__(self):
        return f"{self.line_num}:{self.content}"


class CodeAnalyzer:
    def __init__(self, base_dir: str):
        # 输出数据结构
        self.var_to_methods: Dict[str, Set[str]] = defaultdict(set)
        self.method_to_code: Dict[str, List[SourceLine]] = {}
        self.file_to_snippets: Dict[str, Dict[str, List[SourceLine]]] = defaultdict(dict)

        self.base_dir = Path(base_dir).absolute()

    def analyze(self, global_vars: List[str], target_files: List[str], target_methods: List[str]):
        """
        主分析入口
        :param global_vars: 全局变量列表（如 ["pkg.Class.field"]）
        :param target_files: 目标文件名列表（如 ["BuggyProgram.java"]）
        :param target_methods: 目标方法列表（如 ["Class$Nested.method"]）
        """
        # 1. 在base_dir下递归查找目标文件
        found_files = self._find_target_files(target_files)

        # 2. 分析找到的文件
        for file_path in found_files:
            self._analyze_file(file_path, global_vars, target_methods)

    def _find_target_files(self, target_files: List[str]) -> List[str]:
        """在base_dir下递归查找目标文件（包括所有子目录）"""
        found = []
        target_set = set(target_files)

        for root, _, files in os.walk(self.base_dir):
            for file in files:
                if file in target_set:
                    full_path = os.path.join(root, file)
                    found.append(str(Path(full_path)))

        # 检查是否所有文件都找到
        found_files = {Path(f).name for f in found}
        if missing := target_set - found_files:
            print(f"警告：未找到文件 {missing}")
        return found

    def _analyze_file(self, file_path: str, global_vars: List[str], target_methods: List[str]):
        """分析单个文件"""
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = [line.rstrip() for line in f]

        file_snippets = {
            'imports': [],
            'classes': defaultdict(list),
            'methods': defaultdict(list),
            'constructors': defaultdict(list)  # 每个类的构造方法体
        }

        # 1. 提取import部分
        file_snippets['imports'] = self._extract_imports(lines)

        # 2. 提取类和成员
        current_class = None
        brace_level = 0
        in_method = False
        current_method = None
        method_start = 0

        for i, line in enumerate(lines, 1):
            stripped = line.strip()

            # 检测类开始
            if not current_class and re.match(r'^(public\s+)?(class|interface|enum)\s+\w+', stripped):
                class_name = re.search(r'(class|interface|enum)\s+(\w+)', stripped).group(2)
                current_class = class_name
                class_start = i
                brace_level = 0  # 重置大括号计数

            # 类内容处理
            if current_class:
                brace_level += line.count('{') - line.count('}')

                # 记录类初始化代码
                if brace_level == 1 and not in_method:
                    file_snippets['classes'][current_class].append(SourceLine(i, line))

                # 方法检测
                method_match = self._detect_method(stripped, current_class, brace_level)
                if method_match and not in_method:
                    method_name, is_target, is_constructor = method_match
                    in_method = True
                    current_method = method_name
                    method_start = i
                    if is_target:
                        self.method_to_code[method_name] = []

                # 方法内容记录
                if in_method:
                    sl = SourceLine(i, line)
                    if current_method in self.method_to_code:
                        self.method_to_code[current_method].append(sl)
                    file_snippets['methods'][current_method].append(sl)
                    if is_constructor:
                        file_snippets['constructors'][current_class].append(sl)

                    # 方法结束检测
                    if brace_level == 0:
                        in_method = False
                        current_method = None

                        # 记录变量访问关系
                        if method_name in target_methods:
                            self._track_var_access(
                                file_snippets['methods'][method_name],
                                method_name,
                                global_vars
                            )

                # 类结束检测
                if brace_level == 0 and current_class:
                    current_class = None

        self.file_to_snippets[file_path] = file_snippets

    def _extract_imports(self, lines: List[str]) -> List[SourceLine]:
        """提取import语句"""
        imports = []
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith('import '):
                imports.append(SourceLine(i, line))
            elif stripped and (stripped.startswith('class ') or
                               stripped.startswith('public ')):
                break
        return imports

    def _detect_method(self, line: str, current_class: str, brace_level: int) -> Tuple[str, bool]:
        """检测方法定义并返回(方法名, 是否为目标方法)"""
        match = re.match(
            r'^(public|private|protected|static|final|synchronized)+\s+'
            r'([^{]*?)\s*(\{|\()',
            line
        )
        if match and brace_level == 1:
            method_sig = match.group(2).strip()
            method_name = method_sig.split('(')[0].split()[-1]
            full_name = f"{current_class}.{method_name}"
            return (full_name, True, False)  # 简化示例：假设所有方法都是目标方法,第三个参数是表示是否是构造方法

        # 构造方法匹配（没有返回类型）
        m_ctor = re.match(rf'^\s*(public|private|protected)?\s*{current_class}\s*\(.*\)\s*{{?', line)
        if m_ctor:
            method_name = current_class
            return method_name, True, True

        return None

    def _track_var_access(self, method_lines: List[SourceLine], method_name: str, global_vars: List[str]):
        """追踪方法中的全局变量访问"""
        for line in method_lines:
            for var in global_vars:
                # 简单匹配变量名（实际应用需更精确的语法分析）
                if re.search(rf'\b{var.split(".")[-1]}\b', line.content):
                    self.var_to_methods[var].add(method_name)

    # 输出方法
    def get_var_to_methods(self) -> Dict[str, Set[str]]:
        """获取变量到方法的映射"""
        return self.var_to_methods

    def get_method_to_code(self) -> Dict[str, List[SourceLine]]:
        """获取方法到源码的映射"""
        return self.method_to_code

    def get_file_snippets(self) -> Dict[str, Dict[str, List[SourceLine]]]:
        """获取文件到代码片段的映射"""
        return self.file_to_snippets


class JavaSourceAnalyzer:
    def __init__(self, java_files: List[str], base_dir):
        JAVA_LANGUAGE = Language(tree_sitter_java.language())

        # 2. 初始化解析器
        self.parser = Parser(JAVA_LANGUAGE)

        self.java_files = java_files
        self.base_dir = base_dir
        # 1. 在base_dir下递归查找目标文件
        self.found_files = self._find_target_files(self.java_files)

    def find_method_by_line(self, node, target_line):
        """
        在 AST 中递归查找包含目标行号的方法节点
        :param node: 当前遍历的节点
        :param target_line: 目标行号（从0开始）
        :return: 方法节点或None
        """
        # 检查当前节点是否覆盖目标行号
        start_line, _ = node.start_point
        end_line, _ = node.end_point

        if start_line <= target_line <= end_line:
            # 找到方法节点
            if node.type == 'method_declaration' or node.type == 'constructor_declaration':
                return node

            # 递归查找子节点
            for child in node.children:
                result = self.find_method_by_line(child, target_line)
                if result:
                    return result

        return None

    def extract_method_signature(self, node, source_bytes):
        """
        从方法节点提取完整签名
        :param node: 方法节点
        :param source_bytes: 源代码字节串
        :return: 方法签名字符串
        """
        # 获取关键组件节点
        modifiers_node = node.child_by_field_name('modifiers')
        type_params_node = node.child_by_field_name('type_parameters')
        return_type_node = node.child_by_field_name('type')
        name_node = node.child_by_field_name('name')
        params_node = node.child_by_field_name('parameters')
        throws_node = node.child_by_field_name('throws')

        # 提取各组件文本
        signature_parts = []

        # 1. 修饰符（public/static等）
        if modifiers_node:
            signature_parts.append(source_bytes[modifiers_node.start_byte:modifiers_node.end_byte].decode('utf-8'))

        # 2. 泛型参数
        if type_params_node:
            signature_parts.append(source_bytes[type_params_node.start_byte:type_params_node.end_byte].decode('utf-8'))

        # 3. 返回类型（构造函数没有返回类型）
        if return_type_node:
            signature_parts.append(source_bytes[return_type_node.start_byte:return_type_node.end_byte].decode('utf-8'))

        # 4. 方法名
        if name_node:
            signature_parts.append(source_bytes[name_node.start_byte:name_node.end_byte].decode('utf-8'))

        # 5. 参数列表
        if params_node:
            signature_parts.append(source_bytes[params_node.start_byte:params_node.end_byte].decode('utf-8'))

        # 6. 异常声明
        if throws_node:
            signature_parts.append(source_bytes[throws_node.start_byte:throws_node.end_byte].decode('utf-8'))

        return ' '.join(signature_parts)

    def find_method_sig_from_line(self, file_path, target_line):

        """
          主函数：获取指定行号的方法签名
          :param file_path: Java文件路径
          :param target_line: 目标行号（从1开始）
          :return: 方法签名字符串
          """
        # 读取源代码（字节串）
        with open(self._find_target_file_by_name(file_path), 'rb') as f:
            source_bytes = f.read()

        # 解析AST
        tree = parser.parse(source_bytes)
        root_node = tree.root_node

        # 转换行号（用户输入从1开始，Tree-sitter从0开始）
        tree_sitter_line = target_line - 1

        # 查找方法节点
        method_node = self.find_method_by_line(root_node, tree_sitter_line)

        if method_node:
            return self.extract_method_signature(method_node, source_bytes)
        return None

    def _find_target_file_by_name(self, target_file: str) -> str:
        """在base_dir下递归查找目标文件（包括所有子目录）"""
        # target_set = set(target_files)

        for root, _, files in os.walk(self.base_dir):
            for file in files:
                if file == target_file:
                    full_path = os.path.join(root, file)
                    return str(Path(full_path))

        # 检查是否所有文件都找到

        return None

    def _find_target_files(self, target_files: List[str]) -> List[str]:
        """在base_dir下递归查找目标文件（包括所有子目录）"""
        found = []
        target_set = set(target_files)

        for root, _, files in os.walk(self.base_dir):
            for file in files:
                if file in target_set:
                    full_path = os.path.join(root, file)
                    found.append(str(Path(full_path)))

        # 检查是否所有文件都找到
        found_files = {Path(f).name for f in found}
        if missing := target_set - found_files:
            print(f"警告：未找到文件 {missing}")
        return found

    def _get_code_lines(self, code: str, start: int, end: int) -> List[str]:
        lines = code.splitlines()
        return [f"{i + 1}: {lines[i]}" for i in range(start, end + 1)]

    def _extract_imports(self, tree, code_lines: List[str]) -> List[str]:
        imports = []
        to_visit = [tree.root_node]
        while to_visit:
            node = to_visit.pop()
            if node.type == 'import_declaration':
                start, end = node.start_point[0], node.end_point[0]
                imports.extend(self._get_code_lines("\n".join(code_lines), start, end))
            else:
                to_visit.extend(node.children)
        imports.sort(key=lambda line: int(line.split(':', 1)[0]))
        return imports

    # def _extract_classes(self, tree, code_lines: List[str]) -> List[Dict]:
    #     classes = []
    #     to_visit = [tree.root_node]
    #     while to_visit:
    #         node = to_visit.pop()
    #         if node.type == 'class_declaration':
    #             cls_name = None
    #             for child in node.children:
    #                 if child.type == 'identifier':
    #                     cls_name = child.text.decode()
    #             class_info = {
    #                 'name': cls_name,
    #                 'init_code': [],
    #                 'constructors': [],
    #                 'methods': []
    #             }
    #             for child in node.children:
    #                 if child.type == 'class_body':
    #                     for body in child.children:
    #                         if body.type == 'method_declaration':
    #                             s, e = body.start_point[0], body.end_point[0]
    #                             class_info['methods'].append(
    #                                 self._get_code_lines("\n".join(code_lines), s, e)
    #                             )
    #                         elif body.type == 'constructor_declaration':
    #                             s, e = body.start_point[0], body.end_point[0]
    #                             class_info['constructors'].append(
    #                                 self._get_code_lines("\n".join(code_lines), s, e)
    #                             )
    #                         elif body.type not in {';', '{', '}'} and body.start_point[0] != body.end_point[0]:
    #                             s, e = body.start_point[0], body.end_point[0]
    #                             class_info['init_code'].append(
    #                                 self._get_code_lines("\n".join(code_lines), s, e)
    #                             )
    #             classes.append(class_info)
    #         else:
    #             to_visit.extend(node.children)
    #     return classes

    # def _extract_classes(self, tree, code_lines: List[str]) -> List[Dict]:
    #     classes = []
    #     to_visit = [tree.root_node]

    #     while to_visit:
    #         node = to_visit.pop()
    #         if node.type == 'class_declaration':
    #             # 解析当前类
    #             cls_name = next((c.text.decode() for c in node.children if c.type == 'identifier'), None)
    #             class_info = {
    #                 'name': cls_name,
    #                 'init_code': [],
    #                 'constructors': [],
    #                 'methods': []
    #             }

    #             # 只处理当前类体，不处理嵌套类的内容作为 init_code
    #             for child in node.children:
    #                 if child.type == 'class_body':
    #                     for body in child.children:
    #                         if body.type == 'method_declaration':
    #                             s, e = body.start_point[0], body.end_point[0]
    #                             class_info['methods'].append(self._get_code_lines("\n".join(code_lines), s, e))

    #                         elif body.type == 'constructor_declaration':
    #                             s, e = body.start_point[0], body.end_point[0]
    #                             class_info['constructors'].append(self._get_code_lines("\n".join(code_lines), s, e))

    #                         # 排除类/接口/枚举声明，不作为 init_code
    #                         elif body.type in {'class_declaration', 'interface_declaration', 'enum_declaration'}:
    #                             # 将嵌套类加入待处理队列
    #                             to_visit.append(body)

    #                         # 其他节点可能是初始化代码
    #                         elif body.start_point[0] != body.end_point[0]:
    #                             s, e = body.start_point[0], body.end_point[0]
    #                             class_info['init_code'].append(self._get_code_lines("\n".join(code_lines), s, e))

    #             classes.append(class_info)
    #         else:
    #             # 若不是类声明节点，则继续遍历
    #             if node.type not in {'method_declaration', 'constructor_declaration'}:
    #                 to_visit.extend(node.children)

    #     return classes

    # def _extract_classes(self, tree, code_lines: List[str]) -> List[Dict]:
    #     classes = []
    #     queue = [tree.root_node]

    #     while queue:
    #         node = queue.pop()
    #         if node.type == 'class_declaration':
    #             # 提取类名
    #             cls_name = None
    #             for c in node.named_children:
    #                 if c.type == 'identifier':
    #                     cls_name = c.text.decode()
    #                     break

    #             class_info = {
    #                 'name': cls_name,
    #                 'init_code': [],
    #                 'constructors': [],
    #                 'methods': []
    #             }

    #             # 获取 class_body 节点
    #             body = next((c for c in node.named_children if c.type == 'class_body'), None)
    #             if body:
    #                 for member in body.named_children:
    #                     if member.type == 'method_declaration':
    #                         s, e = member.start_point[0], member.end_point[0]
    #                         class_info['methods'].append(
    #                             self._get_code_lines("\n".join(code_lines), s, e)
    #                         )
    #                     elif member.type == 'constructor_declaration':
    #                         s, e = member.start_point[0], member.end_point[0]
    #                         class_info['constructors'].append(
    #                             self._get_code_lines("\n".join(code_lines), s, e)
    #                         )
    #                     elif member.type == 'class_declaration':
    #                         # 嵌套类，加入队列以递归解析
    #                         queue.append(member)
    #                     else:
    #                         # 其他可能是字段初始化或初始化块
    #                         # 只在有内容的情况下记录
    #                         if member.start_point[0] != member.end_point[0]:
    #                             s, e = member.start_point[0], member.end_point[0]
    #                             class_info['init_code'].append(
    #                                 self._get_code_lines("\n".join(code_lines), s, e)
    #                             )

    #             classes.append(class_info)

    #         else:
    #             # 继续遍历子节点，寻找其他类声明
    #             queue.extend(node.named_children)

    #     return classes

    def _extract_classes(self, tree, code_lines: List[str], file_path: str) -> List[Dict]:
        classes = []
        queue = [tree.root_node]

        while queue:
            node = queue.pop()
            # 修复点1：添加对内部类的处理
            if node.type == 'class_declaration' or node.type == 'enum_declaration':
                # 提取类名
                cls_name = None
                for c in node.named_children:
                    if c.type == 'identifier':
                        cls_name = c.text.decode()
                        break

                class_info = {
                    'name': cls_name,
                    'init_code': None,
                    'constructors': [],
                    'methods': []
                }
                init_code_list = []

                # 获取 class_body 节点
                body = next((c for c in node.named_children if c.type == 'class_body'), None)
                if body:
                    for member in body.named_children:
                        # 修复点2：正确处理初始化块
                        if member.type == 'block' and member.parent.type == 'class_body':
                            # 实例初始化块
                            s, e = member.start_point[0], member.end_point[0]
                            init_code_list.append(
                                self._get_code_lines("\n".join(code_lines), s, e))
                        elif member.type == 'static_block':
                            # 静态初始化块
                            s, e = member.start_point[0], member.end_point[0]
                            init_code_list.append(
                                self._get_code_lines("\n".join(code_lines), s, e))
                        elif member.type == 'method_declaration':
                            s, e = member.start_point[0], member.end_point[0]
                            class_info['methods'].append(
                                self._get_code_lines("\n".join(code_lines), s, e))
                        elif member.type == 'constructor_declaration':
                            s, e = member.start_point[0], member.end_point[0]
                            class_info['constructors'].append(
                                self._get_code_lines("\n".join(code_lines), s, e))
                        elif member.type == 'class_declaration' or member.type == 'enum_declaration':
                            # 嵌套类，加入队列以递归解析
                            queue.append(member)
                        elif member.type == 'field_declaration':
                            # 字段声明（可能包含初始化表达式）
                            s, e = member.start_point[0], member.end_point[0]
                            init_code_list.append(
                                self._get_code_lines("\n".join(code_lines), s, e))
                        else:
                            # 其他类型（如注解、注释等）
                            # 只在有内容的情况下记录
                            if member.start_point[0] != member.end_point[0]:
                                s, e = member.start_point[0], member.end_point[0]
                                init_code_list.append(
                                    self._get_code_lines("\n".join(code_lines), s, e))

                init_code = classInit(file_path=file_path, class_name=cls_name, source_code=init_code_list)
                class_info['init_code'] = init_code
                classes.append(class_info)

            else:
                # 继续遍历子节点，寻找其他类声明
                queue.extend(node.named_children)

        return classes

    def analyze(self) -> Dict[str, Dict]:
        result = {}
        for file_path in self.found_files:
            code = Path(file_path).read_text(encoding='utf-8')

            code_lines = code.splitlines()
            tree = self.parser.parse(code.encode('utf-8'))  # More explicit encoding
            # tree = self.parser.parse(bytes(code, 'utf-8'))

            imports = self._extract_imports(tree, code_lines)

            file_init = fileInit(file_path=file_path, source_code=imports)
            classes = self._extract_classes(tree, code_lines, file_path)

            result[file_path] = {
                'imports': file_init,
                'classes': classes
            }

        return result


class SourceCodeMatcher:
    def __init__(self, result_data):
        self.result = result_data

    def find_method(self, decompiled_signature: str, file_name, line_no: int) -> Tuple[str, str, list]:
        """根据反编译签名查找源代码中的方法"""
        try:
            # 1. 查找对应的Java文件
            for file_path, file_data in self.result.items():
                if not file_path.endswith('.java'):
                    continue
                filename = os.path.basename(file_path)
                if filename != file_name:
                    continue
                # 2. 查找类
                for class_info in file_data['classes']:
                    combined_list = class_info['methods'] + class_info['constructors']
                    for method_lines in combined_list:
                        begin_line_no = int(method_lines[0].split(':')[0])
                        end_line_no = int(method_lines[-1].split(':')[0])
                        if begin_line_no <= line_no <= end_line_no:
                            translator = str.maketrans('', '', ' \n\t\r\f\v')
                            cleaned_decompiled_signature = decompiled_signature.translate(translator)
                            process_lines = lambda lines: ''.join(
                                re.sub(r'\s+', '', re.sub(r'^\d+:', '', line)) for line in lines)
                            if cleaned_decompiled_signature in process_lines(method_lines):
                                return (file_path, class_info['name'], method_lines)
                        else:
                            continue
        except Exception as e:
            print(f"匹配失败: {e}")
            return None
        return None

        #                 translator = str.maketrans('', '', ' \n\t\r\f\v')
        #
        #                 cleaned_decompiled_signature = decompiled_signature.translate(translator)
        #
        #                 if self._match_method(method_lines, sig['method'], line_no):  # 这块匹配逻辑完善，要修改
        #                     return (
        #                         file_path,
        #                         class_info['name'],
        #                         method_lines
        #                     )
        #
        #
        #             if self._match_class_name(class_info['name'], sig['class']):
        #                 # 3. 查找方法
        #                 for method_lines in class_info['methods']:
        #                     if self._match_method(method_lines, sig['method'],line_no): #这块匹配逻辑完善，要修改
        #                         return (
        #                             file_path,
        #                             class_info['name'],
        #                             method_lines
        #                         )
        #
        #                  # 2. 检查构造方法（特殊处理）
        #         if sig['method'] == sig['class'].split('$')[-1]:  # 构造方法名==类名(最后部分)
        #             for ctor_lines in class_info['constructors']:
        #                 if self._match_constructor(ctor_lines, sig['class'],line_no):
        #                     return (
        #                         file_path,
        #                         class_info['name'],
        #                         ctor_lines,
        #                     )
        #     return None
        # except Exception as e:
        #     print(f"匹配失败: {e}")
        #     return None

    def _match_class_name(self, source_name: str, decompiled_name: str) -> bool:
        """匹配类名（处理嵌套类$符号）"""
        # 简单实现：检查类名是否以decompiled_name结尾
        return source_name == decompiled_name or source_name.endswith('.' + decompiled_name)

    def _match_method(self, method_lines: List[str], method_name: str, line_no: int) -> bool:
        """
        检查方法定义是否匹配方法名且包含指定行号
        :param method_lines: 方法源码行列表，格式为["行号: 代码", ...]
        :param method_name: 要匹配的方法名
        :param line_no: 需要包含的行号
        :return: 是否匹配
        """
        if not method_lines:
            return False

        # 检查方法名是否匹配
        first_line = method_lines[0]
        if not (f" {method_name}(" in first_line or f" {method_name} (" in first_line):
            return False

        # 提取方法起始行号和结束行号
        start_line = int(method_lines[0].split(':', 1)[0])
        end_line = int(method_lines[-1].split(':', 1)[0])

        # 检查目标行号是否在方法范围内
        return start_line <= line_no <= end_line

    def _extract_method_name(self, signature_line: str) -> str:
        """从源代码行提取方法名"""
        # 简单实现：取括号前的最后一个单词
        return signature_line.split('(')[0].split()[-1]

    def _match_constructor(self, ctor_lines: List[str], class_name: str, line_no: int) -> bool:
        """
        检查构造方法定义是否匹配且包含指定行号
        :param ctor_lines: 构造方法源码行列表，格式为["行号: 代码", ...]
        :param class_name: 完整类名（可能包含嵌套类符号$）
        :param line_no: 需要包含的行号
        :return: 是否匹配
        """
        if not ctor_lines:
            return False

        # 提取构造方法起始结束行号
        start_line = int(ctor_lines[0].split(':', 1)[0])
        end_line = int(ctor_lines[-1].split(':', 1)[0])

        # 检查行号是否在构造方法范围内
        if not (start_line <= line_no <= end_line):
            return False

        # 获取简单类名（处理嵌套类）
        simple_class_name = class_name.split('$')[-1]
        first_line = ctor_lines[0]

        # 更健壮的构造方法签名匹配
        return any([
            f" {simple_class_name}(" in first_line,  # 标准格式: MyClass(
            f" {simple_class_name} (" in first_line,  # 带空格: MyClass (
            f"{simple_class_name}(" in first_line,  # 可能出现在行首
            f"<init>(" in first_line  # 反编译可能显示的构造方法名
        ])

    # def _parse(self,full_signature: str) -> dict:

    #     """
    #     解析反编译方法签名，例如：
    #     "buggyprogram.BuggyProgram$User.run():void" ->
    #     {
    #         'class': 'BuggyProgram$User',
    #         'package': 'buggyprogram',
    #         'method': 'run',
    #         'return_type': 'void',
    #         'params': []
    #     }
    #     """
    #     pattern = r'^(?P<package>[a-zA-Z0-9_.]+)\.(?P<class>[a-zA-Z0-9_$]+)\.(?P<method>[a-zA-Z0-9_]+)\((?P<params>.*)\):(?P<return_type>.+)$'
    #     match = re.match(pattern, full_signature)
    #     if not match:
    #         raise ValueError(f"无法解析方法签名: {full_signature}")
    #     return {
    #         'package': match.group('package'),
    #         'class': match.group('class'),
    #         'method': match.group('method'),
    #         'params': [p.strip() for p in match.group('params').split(',') if p.strip()],
    #         'return_type': match.group('return_type')
    #     }
    # def _parse(self, full_signature: str) -> dict:
    #     pattern = r'^(?:(?P<package>[a-zA-Z0-9_.]+)\.)?(?P<class>[a-zA-Z0-9_$]+)\.(?P<method>[a-zA-Z0-9_]+)\((?P<params>.*)\):(?P<return_type>.+)$'
    #     match = re.match(pattern, full_signature)
    #     if not match:
    #         raise ValueError(f"无法解析方法签名: {full_signature}")

    #     return {
    #         'package': match.group('package') or '',  # 无包名时返回空字符串
    #         'class': match.group('class'),
    #         'method': match.group('method'),
    #         'params': [p.strip() for p in match.group('params').split(',') if p.strip()],
    #         'return_type': match.group('return_type')
    #     }
    def _parse(self, full_signature: str) -> dict:
        """
    增强版方法签名解析器，支持：
    1. 可选包名
    2. 可选返回类型
    3. 更灵活的命名规则
    4. 处理泛型类型

    支持格式示例：
    "com.example.pkg.MyClass$Inner.method(String, int): void"
    "MyClass.method()"
    "Utils.process(List<String>):boolean"
    "BuggyProgram$User.generate()"
        """
        # 更灵活的正则表达式
        pattern = r"""
            ^
            (?: (?P<package> [\w.-]+ ) \. )?  # 可选包名（允许字母、数字、_、-、.）
            (?P<class> [\w$<>]+ )             # 类名（允许<>用于泛型）
            \.
            (?P<method> \w+ )                 # 方法名
            \(
            (?P<params> [^)]* )               # 参数部分（任意非右括号字符）
            \)
            (?: : \s* (?P<return_type> \S+ ) )?  # 可选返回类型
            $
        """

        match = re.search(pattern, full_signature, re.VERBOSE)
        if not match:
            raise ValueError(f"无法解析方法签名: {full_signature}")

        # 解析参数
        params_str = match.group('params').strip()
        params = []
        if params_str:
            # 处理包含泛型的复杂参数
            depth = 0
            current = []
            for char in params_str:
                if char == '<':
                    depth += 1
                elif char == '>':
                    depth -= 1
                elif char == ',' and depth == 0:
                    params.append(''.join(current).strip())
                    current = []
                    continue
                current.append(char)

            if current:
                params.append(''.join(current).strip())

        class_from_match = match.group('class')
        class_to_return = class_from_match  # 默认使用匹配到的完整类名

        if '$' in class_from_match:
            # 如果包含$，则分割并取最后一部分作为简单类名
            parts = class_from_match.split('$')
            class_to_return = parts[-1]

        return {
            'package': match.group('package') or '',
            'class': class_to_return,  # 使用修正后的逻辑赋值
            'method': match.group('method'),
            'params': params,
            'return_type': match.group('return_type') or 'void'
        }


def extract_method_decls_with_treesitter(numbered_lines: list[str]) -> list[tuple[int, str]]:
    """
    使用 Tree-sitter 提取方法声明行
    返回: [(行号, 声明代码), ...]
    """
    # 合并代码并记录行号映射
    code_lines = [line.split(':', 1)[1] for line in numbered_lines]
    line_offsets = [0]
    for line in code_lines:
        line_offsets.append(line_offsets[-1] + len(line) + 1)  # +1 for newline

    full_code = "\n".join(code_lines)
    tree = parser.parse(bytes(full_code, "utf8"))

    # 遍历语法树找方法声明节点
    method_nodes = []
    cursor = tree.walk()
    stack = []

    while True:
        node = cursor.node
        if node.type == 'method_declaration':
            method_nodes.append(node)

        # 深度优先遍历
        if cursor.goto_first_child():
            stack.append(cursor.copy())
            continue
        if cursor.goto_next_sibling():
            continue
        if not stack:
            break
        cursor = stack.pop()

    # 映射回原始行号
    declarations = []
    for node in method_nodes:
        start_line = find_line_number(node.start_byte, line_offsets)
        decl_code = code_lines[start_line].strip()
        declarations.append((int(numbered_lines[start_line].split(':', 1)[0]), decl_code))

    return declarations


def find_line_number(byte_offset: int, line_offsets: list[int]) -> int:
    """根据字节偏移量计算行号"""
    for i, offset in enumerate(line_offsets):
        if byte_offset < offset:
            return i - 1
    return len(line_offsets) - 1


from tree_sitter import Parser, Language, Node
import os

# 使用示例
if __name__ == "__main__":
    # numbered_lines = [
    #     "42: @Override",
    #     "43: public List<String> parse(String input, Class<?> clazz)",
    #     "44:     throws IOException {",
    #     "45:     return mapper.readValue(input, clazz);",
    #     "46: }"
    # ]

    # declaration = extract_method_declaration(numbered_lines)
    # print("提取的方法声明:")
    # print(declaration)

    import javalang


    def find_field_declaration(java_code, target_field):
        tree = javalang.parse.parse(java_code)
        for path, node in tree.filter(javalang.tree.FieldDeclaration):
            for declarator in node.declarators:
                if declarator.name == target_field:
                    return {
                        "line": node.position.line,
                        "code": f"{' '.join(node.modifiers)} {node.type} {declarator.name} = {declarator.initializer};"
                    }
        return None


    # 示例用法
    code = """
    package buggyprogram;

    public class BuggyProgram {
        private List<String> history = new ArrayList<>();
    }
    """

    result = find_field_declaration(code, "history")
    if result:
        print(f"Found at line {result['line']}: {result['code']}")
    else:
        print("Declaration not found.")
