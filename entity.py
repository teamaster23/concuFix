from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
import json
from collections import defaultdict
import re  # 导入正则表达式模块



@dataclass
class MethodCode:
    class_name: str  # 所属类名（如 "BuggyProgram$User"）
    method_name: str  # 方法名（如 "run"）
    source_code: str  # 方法源码
    start_line: int  # 起始行号
    end_line: int  # 结束行号


@dataclass
class ClassInitCode:
    class_name: str
    init_code: str  # 类初始化代码（含字段声明）


@dataclass
class AccessEvent:
    """访问事件信息"""
    variable: str
    file: str
    line: str
    method: str
    call_chain:[]
    event_type: str


@dataclass(order=True)
class Method:
    """
    最小化的方法数据类
    包含：文件路径、源码、方法名三个核心属性
    """
    name: str  # 必须提供的属性（用于排序和比较）
    file_name: str
    file_path: str = field(default="", compare=False)  # 未知时默认为空字符串
    source_code: List[str] = field(default_factory=list, compare=False)  # 未知时默认为空列表
    class_name: str = field(default="", compare=False)  # 未知时默认为空字符串
    patch: List[str] = field(default_factory=list, compare=False)

    @property
    def start_line(self) -> int:
        """获取方法起始行号"""
        return int(self.source_code[0].split(':', 1)[0]) if self.source_code else -1

    @property
    def end_line(self) -> int:
        """获取方法结束行号"""
        return int(self.source_code[-1].split(':', 1)[0]) if self.source_code else -1

    def __hash__(self):
        return hash(self.name)  # 仅基于 name 计算哈希值


@dataclass
class classInit:
    file_path: str  # 未知时默认为空字符串
    class_name: str
    source_code: List[str] = field(default_factory=list)
    patches: str = field(default="")


@dataclass
class fileInit:
    file_path: str  # 未知时默认为空字符串
    source_code: List[str] = field(default_factory=list)
    patches: str = field(default="")


class RacePair:
    def __init__(self, event1, event2):
        # 设置实例属性
        self.event1 = event1
        self.event2 = event2


class ConfictMethod:
    def __init__(self, method1, method2):
        self.methods = tuple(sorted([method1, method2]))

    @property
    def method1(self):
        return self.methods[0]

    @property
    def method2(self):
        return self.methods[1]

    def __hash__(self):
        return hash(self.methods)

    def __eq__(self, other):
        if not isinstance(other, ConfictMethod):
            return False
        return self.methods == other.methods

    def __str__(self):
        return f"{self.method1} <-> {self.method2}"


class BugReports:
    def __init__(self, path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                self.data = json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError(f"找不到文件: {path}")
        except json.JSONDecodeError:
            raise ValueError(f"无效的JSON格式: {path}")

        self.files = set()


        for index, bug in enumerate(self.data, 1):
            self.files.add(bug["file"])


        self.races = []
        self.method_pair = None
        self.variable_to_methods = defaultdict(set)  # set()
        self.method_pair_to_races = dict()
        self.method_to_method_info = dict()
        self.source_analyzer=None

        # self._load_race_pairs()

    def split_traces(self,traces):
        # 初始化结果和临时组
        groups = []
        current_group = []

        # 标记是否找到第一个分割点
        found_first = False

        for trace in traces:
            # 检查是否是分割点
            if 'description' in trace and (
                    trace['description'] == '<Read trace>' or trace['description'] == '<Write trace>'):
                if not found_first:
                    # 第一个分割点，开始第一组
                    found_first = True
                    if current_group:  # 如果有内容，加入结果
                        groups.append(current_group)
                    current_group = [trace]  # 开始新组
                else:
                    # 第二个分割点，结束第一组，开始第二组
                    groups.append(current_group)
                    current_group = [trace]
            else:
                current_group.append(trace)

        # 添加最后一组
        if current_group:
            groups.append(current_group)

        # # 确保总是返回两组（可能有一组为空）
        # while len(groups) < 2:
        #     return traces

        return groups  # 只返回前两组

    # 测试用例
    # test_traces = [
    #     {'description': '<Read trace>', 'data': 'read1'},
    #     {'data': 'read data1'},
    #     {'data': 'read data2'},
    #     {'description': '<Write trace>', 'data': 'write1'},
    #     {'data': 'write data1'},
    #     {'description': '<Read trace>', 'data': 'read2'},
    #     {'data': 'read data3'}
    # ]

    # group1, group2 = split_traces(test_traces)
    # print("第一组:")
    # for item in group1:
    #     print(item)
    # print("\n第二组:")
    # for item in group2:
    #     print(item)

    def _parse_race_from_traces(self,traces):
        groups=self.split_traces(traces)
        events = []
        variable=''

        if len(groups) < 2:
            file_name=groups[0][-1]['filename']
            line_number=groups[0][-1]['line_number']
            is_read=False
            pattern = r'`([^`]*)`'  # 匹配 `...` 中的内容
            matches = re.findall(pattern, groups[0][-1]['description'])
            variable = matches[0]
            method_dig = self.source_analyzer.find_method_sig_from_line(file_name, line_number)
            if method_dig is None:
                pass
            if len(groups[0])<3:
                call_chain=[]
            else:
                call_chain=[(t["filename"],self.source_analyzer.find_method_sig_from_line(t["filename"], t["line_number"])) for t in groups[0][1:-1]]

            event = AccessEvent(
                variable=variable,
                file=file_name,
                line=str(line_number),
                method=method_dig,
                call_chain=call_chain,
                event_type='read' if is_read else 'write'
            )
            events.append(event)
            events.append(event)

        else:
            for group in groups:
                pattern = r'`([^`]*)`'  # 匹配 `...` 中的内容
                matches = re.findall(pattern, group[-1]['description'])
                variable = matches[0]
                file_name = group[-1]['filename']
                line_number = group[-1]['line_number']
                method_dig = self.source_analyzer.find_method_sig_from_line(file_name, line_number)
                is_read = group[0]['description'] == '<Read trace>'
                if len(group) < 3:
                    call_chain = []
                else:
                    call_chain = [
                        (t["filename"], self.source_analyzer.find_method_sig_from_line(t["filename"], t["line_number"])) for t in
                        group[1:-1]]

                event = AccessEvent(
                    variable=variable,
                    file=file_name,
                    line=str(line_number),
                    method=method_dig,
                    call_chain=call_chain,
                    event_type='read' if is_read else 'write'
                )
                events.append(event)


        race = RacePair(event1=events[0], event2=events[1])
        self.races.append(race)


        method1 = self.method_to_method_info.setdefault((events[0].file,events[0].method), Method(name=events[0].method,file_name=events[0].file))
        method2 = self.method_to_method_info.setdefault((events[1].file,events[1].method), Method(name=events[1].method,file_name=events[1].file))

        self.variable_to_methods[variable].add((events[0].file,events[0].method))
        self.variable_to_methods[variable].add((events[1].file,events[1].method))

        method_pair = ConfictMethod(method1, method2)
        self.method_pair_to_races.setdefault(method_pair, []).append(race)



    def load_race_pairs(self):
        # 遍历每个bug条目
        for index, bug in enumerate(self.data, 1):
            traces=bug.get('bug_trace','')
            self._parse_race_from_traces(traces)

            #
            # qualifier = bug.get('qualifier', '')
            # procedure = bug.get('procedure', '')
            #
            # # 1. 从qualifier文本中提取变量名
            # var_match = re.search(r'from `([^`]+)`', qualifier)
            # if var_match:
            #     variable = var_match.group(1).replace('.[_]', '')
            # else:
            #     # 备用方案：尝试从 bug_trace 的描述中查找
            #     var_match_alt = re.search(r"access to `([^`]+)`", str(bug.get('bug_trace', '')))
            #     if var_match_alt:
            #         variable = var_match_alt.group(1).replace('.[_]', '')
            #     else:
            #         print(f"警告：无法在第 {index} 个bug中找到变量名，已跳过。")
            #         continue
            #
            # # 2. 从qualifier中提取两个冲突的方法名
            # method_matches = re.findall(r'method `([^`]+?)\(.*?\)`', qualifier)
            # if len(method_matches) < 2:
            #     print(f"警告：无法在第 {index} 个bug中找到两个冲突方法，已跳过。")
            #     continue
            #
            # # 方法1的完整签名来自 'procedure' 字段
            # method1_sig = procedure
            #
            # # 构建方法2的签名
            # class_name = get_class_from_procedure(procedure)
            # if not class_name:
            #     print(f"警告：无法从 '{procedure}' 中解析类名，已跳过第 {index} 个bug。")
            #     continue
            #
            # method2_simple_name = method_matches[1].split('.')[-1]
            # method2_sig = f"{class_name}.{method2_simple_name}()"
            #
            # # 3. 从 bug_trace 中为读/写事件提取行号
            # bug_trace = bug.get('bug_trace', [])
            # read_line, write_line = None, None
            # for i, trace_item in enumerate(bug_trace):
            #     desc = trace_item.get('description', '')
            #     # 从描述节点的下一个节点中获取行号
            #     if '<Read trace>' in desc and i + 1 < len(bug_trace):
            #         read_line = bug_trace[i + 1].get('line_number')
            #     elif '<Write trace>' in desc and i + 1 < len(bug_trace):
            #         write_line = bug_trace[i + 1].get('line_number')
            #
            # if read_line is None or write_line is None:
            #     print(f"警告：无法在第 {index} 个bug的追踪信息中找到读/写行号，已跳过。")
            #     continue
            #
            # # 4. 确定事件类型（读/写 或 写/读）
            # is_read_write = 'Read/Write race' in qualifier
            #
            # # 5. 创建两个 AccessEvent 对象
            # file_name = bug.get('file', '')
            # # 第一个方法（method1_sig）及其关联事件
            # event1 = AccessEvent(
            #     variable=variable,
            #     file=file_name,
            #     line=str(read_line if is_read_write else write_line),
            #     method=method1_sig,
            #     event_type='read' if is_read_write else 'write'
            # )
            # # 第二个方法（method2_sig）及其关联事件
            # event2 = AccessEvent(
            #     variable=variable,
            #     file=file_name,
            #     line=str(write_line if is_read_write else read_line),
            #     method=method2_sig,
            #     event_type='write' if is_read_write else 'read'
            # )
            #
            # # 使用提取的信息填充数据结构
            # race = RacePair(event1=event1, event2=event2)
            # self.races.append(race)
            # self.files.add(event1.file)
            #
            # method1 = self.method_to_method_info.setdefault(event1.method, Method(name=event1.method))
            # method2 = self.method_to_method_info.setdefault(event2.method, Method(name=event2.method))
            #
            # self.variable_to_methods[variable].add(event1.method)
            # self.variable_to_methods[variable].add(event2.method)
            #
            # method_pair = ConfictMethod(method1, method2)
            # self.method_pair_to_races.setdefault(method_pair, []).append(race)

    def extract_access_events(bug, bug_index):
        """提取bug中的所有access事件并打印"""
        race_details = bug.get('race_details', {})
        access_events = []

        print("\n竞态事件详情:")

        # 提取读事件
        read_access = race_details.get('read_access', {})
        if read_access:
            event = {
                'bug_index': bug_index,
                'event_type': 'read',
                'variable': read_access.get('variable', '未知变量'),
                'method': bug.get('location', {}).get('procedure', '未知方法'),
                'trace': read_access.get('trace', [])
            }
            access_events.append(event)
            # print_access_event(event)