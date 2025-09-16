from tree_sitter import Language, Parser
import os
import tree_sitter_java  # 官方预编译 Java 绑定

JAVA_LANGUAGE = Language(tree_sitter_java.language())

parser = Parser(JAVA_LANGUAGE)



def find_method_by_line(node, target_line):
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
        if node.type == 'method_declaration':
            return node

        # 递归查找子节点
        for child in node.children:
            result = find_method_by_line(child, target_line)
            if result:
                return result

    return None


def extract_method_signature(node, source_bytes):
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


def get_method_signature_by_line(file_path, target_line):
    """
    主函数：获取指定行号的方法签名
    :param file_path: Java文件路径
    :param target_line: 目标行号（从1开始）
    :return: 方法签名字符串
    """
    # 读取源代码（字节串）
    with open(file_path, 'rb') as f:
        source_bytes = f.read()

    # 解析AST
    tree = parser.parse(source_bytes)
    root_node = tree.root_node

    # 转换行号（用户输入从1开始，Tree-sitter从0开始）
    tree_sitter_line = target_line - 1

    # 查找方法节点
    method_node = find_method_by_line(root_node, tree_sitter_line)

    if method_node:
        return extract_method_signature(method_node, source_bytes)
    return None


# 测试用例
if __name__ == '__main__':
    # 测试用例1：普通方法
    java_code_1 = """
    public class Calculator {
        // 加法方法
        public int add
        (int a, int b) {
            return a + b;
        }

        // 带泛型和异常的方法
        public <T> List<T> processData(List<T> data) throws ProcessingException {
            // 方法实现
        }

        // 构造函数
        public Calculator(String name) {
            this.name = name;
        }
    }
    """

    # 测试用例2：接口和静态方法
    java_code_2 = """
    public interface MathOperations {
        default double square(double num) {
            return num * num;
        }

        static int factorial(int n) {
            if (n == 0) return 1;
            return n * factorial(n - 1);
        }
    }
    """

    # 写入临时文件
    test_files = {
        'TestClass.java': java_code_1,
        'TestInterface.java': java_code_2
    }

    for filename, content in test_files.items():
        with open(filename, 'w') as f:
            f.write(content)

    # 执行测试
    test_cases = [
        ('TestClass.java', 4, 'public int add ( int a , int b )'),
        ('TestClass.java', 8, 'public < T > List < T > processData ( List < T > data ) throws ProcessingException'),
        ('TestClass.java', 13, 'public Calculator ( String name )'),
        ('TestInterface.java', 4, 'default double square ( double num )'),
        ('TestInterface.java', 8, 'static int factorial ( int n )')
    ]

    print("测试结果:")
    print("-" * 60)
    for file_path, line, expected in test_cases:
        result = get_method_signature_by_line(file_path, line)
        status = "成功" if result == expected else "失败"
        print(f"文件: {file_path} 行号: {line}")
        print(f"预期: {expected}")
        print(f"实际: {result}")
        print(f"状态: {status}")
        print("-" * 60)

    # 清理测试文件
    for filename in test_files.keys():
        os.remove(filename)