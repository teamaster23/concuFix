from typing import Dict, List, Tuple, Any, Set
import os
import ast
from concuFix.parser import JavaSourceAnalyzer, SourceCodeMatcher
from entity import BugReports, Method
from tree_sitter import Language, Parser, Node
import tree_sitter_java  # 官方预编译 Java 绑定
import re


class Event:
    def __init__(self, operation: str, variable: str, method: str):
        self.operation = operation
        self.variable = variable
        self.method = method


class Initializer():
    """初始化器节点，负责分析源码并提取初始信息"""

    def __init__(self, config: Dict[str, Any]):
        # super().__init__(name="Initializer")
        self.config = config
        self.source_code = {}
        self.ast_trees = {}
        self.project_dir = self.config.get("project_dir")

        self.bug_report = None
        self.source_analyzer = None

    def process(self, state) -> Dict[str, Any]:
        """存储提取的缺陷信息"""
        bug_report = self._load_bug_report()
        """提取源码信息"""
        source_info = self._load_source_code(bug_report.files, bug_report)

        # for path,content in source_info.items():
        #     bug_report.method_to_method_info.setdefault((os.path.basename(path),"import"),Method(name="import",file_name=(os.path.basename(path)),file_path=path,source_code=)

        """根据源码信息，补充缺陷信息"""
        matcher = SourceCodeMatcher(source_info)
        for method_pair in bug_report.method_pair_to_races:
            method1 = method_pair.method1
            method2 = method_pair.method2
            if method1.source_code == None or len(method1.source_code) == 0:
                if bug_report.method_pair_to_races[method_pair][0].event1.method == method1.name:
                    (file_path, class_name, ctor_lines) = matcher.find_method(method1.name, method1.file_name, int(
                        bug_report.method_pair_to_races[method_pair][0].event1.line))
                else:
                    (file_path, class_name, ctor_lines) = matcher.find_method(method1.name, method1.file_name, int(
                        bug_report.method_pair_to_races[method_pair][0].event2.line))
                method1.source_code = ctor_lines
                method1.file_path = file_path
                method1.class_name = class_name

            if method2.source_code == None or len(method2.source_code) == 0:
                if bug_report.method_pair_to_races[method_pair][0].event1.method == method2.name:
                    (file_path, class_name, ctor_lines) = matcher.find_method(method2.name, method2.file_name, int(
                        bug_report.method_pair_to_races[method_pair][0].event1.line))
                else:
                    (file_path, class_name, ctor_lines) = matcher.find_method(method2.name, method2.file_name, int(
                        bug_report.method_pair_to_races[method_pair][0].event2.line))
                method2.source_code = ctor_lines
                method2.file_path = file_path
                method2.class_name = class_name

        """记录变量和源码信息"""
        self._extract_variable_to_methods(bug_report, source_info)

        state['source_info'] = source_info
        state['bug_report'] = bug_report

        state['policies'] = {}
        variable_to_init = self._extract_variable_init(state)
        state['variable_to_init'] = variable_to_init
        state['suggest_polices'] = self._get_suggest_polices(state=state)

        # self._extract_variable_to_methods_with_construction(bug_report,source_info)

        return state

    def _extract_variable_init(self, state) -> None:
        bug_report = state['bug_report']
        source_info = state['source_info']
        variable_to_init = {}
        for variable in bug_report.variable_to_methods:
            class_name = bug_report.method_to_method_info[list(bug_report.variable_to_methods[variable])[0]].class_name
            file_path = bug_report.method_to_method_info[list(bug_report.variable_to_methods[variable])[0]].file_path
            for class_tmp in source_info[file_path]['classes']:
                if class_tmp['name'] == class_name:
                    variable_to_init[variable] = class_tmp['init_code']
            # 获取的是class的名字
            # print(bug_report.method_to_method_info[bug_report.variable_to_methods[variable].pop()].class_name)
            # 获取文件的名字
            # print(bug_report.method_to_method_info[bug_report.variable_to_methods[variable].pop()].file_path)
            # pass
        return variable_to_init

    def _get_suggest_polices(self, state) -> None:
        variable_to_strategy = dict()
        for variable, methods in state['bug_report'].variable_to_methods.items():
            strategy = self._determine_repair_strategies(variable=variable, methods=methods, state=state)
            variable_to_strategy[variable] = strategy

        return variable_to_strategy

    def _determine_repair_strategies(self, variable, methods, state) -> None:
        variable_to_init = state['variable_to_init']
        processed_methods_body = []
        strategy = None
        for method in methods:
            # print(state['bug_report'].method_to_method_info[method].source_code)
            # print(variable)
            # exit()
            processed_methods_body.append(
                self._process_code_body(state['bug_report'].method_to_method_info[method].source_code, variable))
        # TO-DO将方法替换成有冲突的，而不是所有的
        # TO-DO得到策略

        print("++++++++++++++++")
        print("提示词所求：")
        print(variable_to_init)
        print(processed_methods_body)
        print("++++++++++++++++")

        return strategy

    def _process_code_body(self, code_lines, target_variable):
        """
        处理代码体：去除行号，并简化不访问目标变量的连续行

        参数:
            code_lines: list of str - 原始代码行列表（格式: ["行号: 代码", ...]）
            target_variable: str - 需要检测的目标变量名（可以是完整路径如 this.this$0.x 或简单变量名）

        返回:
            list of str - 处理后的代码行列表
        """
        # 1. 去除行号部分
        cleaned_lines = []
        for line in code_lines:
            if ':' in line:
                code_part = line.split(':', 1)[1].strip()
                cleaned_lines.append(code_part)
            else:
                cleaned_lines.append(line.strip())

        # 2. 提取纯变量名（处理 this.this$0.x 的情况）
        pure_var_name = target_variable.split('.')[-1]

        # 3. 简化不访问目标变量的连续行
        processed_lines = []
        skip_block = []
        print(pure_var_name)
        for line in cleaned_lines:
            # 检查当前行是否包含目标变量（直接字符串匹配）
            if pure_var_name in line:  # 用 split() 避免部分匹配
                # 如果有待跳过的行块
                if skip_block:
                    if len(skip_block) > 1:
                        processed_lines.append("//..")
                    else:
                        processed_lines.append(skip_block[0])
                    skip_block = []
                processed_lines.append(line)
            else:
                skip_block.append(line)

        # 处理末尾的跳过块
        if skip_block:
            if len(skip_block) > 1:
                processed_lines.append("//..")
            else:
                processed_lines.append(skip_block[0])

        print(code_lines)
        print('-----')
        print(processed_lines)
        return processed_lines

    def _extract_variable_to_methods(self, bug_report, source_info) -> None:
        # 方法1：直接遍历keys
        for variable in bug_report.variable_to_methods:
            # print(variable)  # 输出每个变量名
            methods = bug_report.variable_to_methods[variable]  # 获取对应的methods集合
            method_infos = [bug_report.method_to_method_info[method] for method in methods if
                            method in bug_report.method_to_method_info]
            # file_class_pairs = [(method.file_path, method.class_name) for method in method_infos]

            file_class_pairs = list({(method.file_path, method.class_name) for method in method_infos})

            # 遍历每个元素
            for file_path, class_name in file_class_pairs:
                for class_info in source_info[file_path]['classes']:
                    if class_info['name'] == class_name:
                        method_items = class_info['constructors'] + class_info[
                            'methods']  # set(class_info['constructors'] + class_info['methods'])
                        for method in method_items:
                            # 首先看variable是否出现在某个方法,并且该方法尚未被记录
                            if (self._is_variable_used(method, variable) and (
                            not self._is_code_exists(method, method_infos))):
                                method_name = self._find_method_by_code(bug_report.method_to_method_info, method)
                                if method_name == '':
                                    method_name = self._extract_method_declaration(method)
                                    bug_report.method_to_method_info[method_name] = Method(name=method_name,
                                                                                           file_name=os.path.basename(
                                                                                               file_path),
                                                                                           file_path=file_path,
                                                                                           source_code=method,
                                                                                           class_name=class_name)

                                    # 记录无bug的，但是存在variable的变量
                                    bug_report.variable_to_methods[variable].add(
                                        (os.path.basename(file_path), method_name))

    # 初始化 Tree-sitter Java 解析器
    def _init_java_parser(self):
        JAVA_LANGUAGE = Language(tree_sitter_java.language())

        # 2. 初始化解析器
        parser = Parser(JAVA_LANGUAGE)

        return parser

    def _extract_method_declaration(self, numbered_lines: list[str]) -> str:
        """
        从带行号的 Java 方法代码中提取纯方法声明（不含方法体）
        输入: ["42: public void run() {", "43:     System.out.println();", "44: }"]
        输出: "public void run()"
        """
        # 1. 准备代码（去掉行号）
        code_lines = [line.split(':', 1)[1].strip() for line in numbered_lines]
        full_code = " ".join(code_lines)  # 合并为单行便于解析

        # 2. 解析代码
        parser = self._init_java_parser()
        tree = parser.parse(bytes(full_code, "utf8"))

        # 3. 查找方法声明节点
        root = tree.root_node
        method_node = self._find_method_declaration_node(root)

        if not method_node:
            return ""

        # 4. 提取声明部分（排除方法体）
        return self._extract_declaration_text(full_code, method_node)

    def _find_method_declaration_node(self, node: Node) -> Node:
        """递归查找方法声明节点"""
        if node.type == "method_declaration":
            return node
        for child in node.children:
            result = self._find_method_declaration_node(child)
            if result:
                return result
        return None

    def _extract_declaration_text(self, full_code: str, method_node: Node) -> str:
        """从方法节点提取声明文本（不含方法体）"""
        # 查找方法体节点（如果有）
        body_node = None
        for child in method_node.children:
            if child.type == "block":  # 方法体节点
                body_node = child
                break

        # 计算声明结束位置
        end_byte = body_node.start_byte if body_node else method_node.end_byte
        start_byte = method_node.start_byte

        # 提取声明文本
        declaration_bytes = bytes(full_code, "utf8")[start_byte:end_byte]
        return declaration_bytes.decode("utf8").strip()

    def _is_code_exists(self, code_block, method_infos):
        for method_info in method_infos:
            if code_block == method_info.source_code:
                return True
        return False

    def _find_method_by_code(self, method_to_method_info: dict, code_block: list[str]) -> str:
        """
        在 method_to_method_info 字典中查找是否有值的指定属性匹配输入的代码块

        Args:
            method_to_method_info: 方法信息字典 {method_name: method_info}
            code_block: 要查找的代码块（行列表）
            attribute_name: 要比对的属性名（默认为 'source_code'）

        Returns:
            如果找到匹配的方法名（key），否则返回 None
        """
        for method_name, method_info in method_to_method_info.items():
            if method_info.source_code == code_block:
                return method_name

        return ''

    def _is_variable_used(self, code_block, target_variable):
        """
        判断代码块中是否使用了目标变量
        :param code_block: 代码块列表，格式为 ["行号: 源码", ...]
        :param target_variable: 目标变量名（如 "this.this$0.randomNumber"）
        :return: True如果使用了变量，否则False
        """
        # 提取变量名的核心部分（最后一个标识符）
        var_name = target_variable.split('.')[-1]

        # 创建匹配模式
        patterns = [
            r'\b' + re.escape(var_name) + r'\b',  # 单独变量名
            r'\bthis\$0\.' + re.escape(var_name) + r'\b',  # this$0.变量名
            r'\bthis\.this\$0\.' + re.escape(var_name) + r'\b'  # this.this$0.变量名
        ]

        for line in code_block:
            # 提取源码部分（去掉行号）
            if ':' in line:
                code = line.split(':', 1)[1].strip()
            else:
                code = line

            # 检查所有模式
            for pattern in patterns:
                if re.search(pattern, code):
                    return True

        return False

    def _load_bug_report(self) -> BugReports:
        bug_report_path = os.path.join(self.project_dir, "report.json")
        bug_report = BugReports(bug_report_path)
        self.source_analyzer = JavaSourceAnalyzer(list(bug_report.files), base_dir=self.project_dir)

        bug_report.source_analyzer = self.source_analyzer
        bug_report.load_race_pairs()
        return bug_report

    def _load_source_code(self, java_files: set, bug_report):
        source_analyzer = JavaSourceAnalyzer(list(java_files), base_dir=self.project_dir)
        source_info = source_analyzer.analyze()
        return source_info

    def get_java_file(self, state) -> str:
        """
        根据RacePair获取对应的Java文件内容
        返回: 文件完整路径
        """
        # 实现步骤1: 验证文件存在并读取内容
        pass

    def annotate_code_lines(self, file_path: str):
        """
        标注代码行号（步骤2）
        返回: 标注后的行列表，格式为"  代码"
        """
        # 实现步骤2: 读取文件并添加行号标注
        pass

    def extract_method_pair(self, state):
        """
        提取竞争方法对（步骤3）
        返回: 包含两个方法代码的MethodPair对象
        """
        # 实现步骤3: 基于行号范围提取方法代码
        pass

    def process_race_pair(self, state):
        """
        处理完整流程的入口方法
        """
        pass
        # file_path = self.get_java_file(race_pair)
        # self.annotate_code_lines(file_path)
        # return self.extract_method_pair(race_pair)

    def _parse_source_code(self) -> None:
        """解析源代码为AST"""
        for rel_path, code in self.source_code.items():
            try:
                tree = ast.parse(code)
                self.ast_trees[rel_path] = tree
            except SyntaxError as e:
                print(f"解析文件 {rel_path} 时出错: {e}")

    def _extract_initial_method_pairs(self) -> List[Tuple[str, str]]:
        """提取可能存在并发冲突的初始方法对"""
        # 实际实现需要基于AST分析、调用图等
        # 这里简化为返回一些示例方法对
        return [
            ("module1.py:method_a", "module2.py:method_b"),
            ("module3.py:method_c", "module1.py:method_d")
        ]

    def _extract_related_events(self, method_pairs: List[Tuple[str, str]]) -> Dict[
        Tuple[str, str], List[Tuple[Event, Event]]]:
        """提取方法对之间的相关事件"""
        # 实际实现需要分析方法间共享的变量和操作
        related_events = {}
        for pair in method_pairs:
            related_events[pair] = [
                (Event("read", "shared_var1", pair[0]), Event("write", "shared_var1", pair[1])),
                (Event("write", "shared_var2", pair[0]), Event("read", "shared_var2", pair[1]))
            ]
        return related_events    