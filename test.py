# import tree_sitter_java
# from tree_sitter import Language, Parser

# JAVA_LANGUAGE = Language(tree_sitter_java.language())
# parser = Parser(JAVA_LANGUAGE)
# # 安装依赖（确保已运行）
# # pip install tree-sitter tree-sitter-java

from tree_sitter import Language, Parser
import tree_sitter_java  # 官方预编译 Java 绑定

# 1. 加载 Java 语法
JAVA_LANGUAGE = Language(tree_sitter_java.language())

# 2. 初始化解析器
parser = Parser(JAVA_LANGUAGE)

# 3. 测试的 Java 代码片段
java_code = """
package example;

public class Foo {
    public Foo(int x) {
        this.x = x;
    }

    public void bar() {
        System.out.println(x);
    }
}
"""

# 4. 解析为字节并生成语法树
tree = parser.parse(java_code.encode('utf-8'))

# 5. 输出根节点类型和源码范围
root = tree.root_node
print("Root node type:", root.type)
print("Tree covers lines:", root.start_point[0] + 1, "to", root.end_point[0] + 1)
