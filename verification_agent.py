from typing import Dict, List, Tuple, Any
import subprocess
import tempfile
import os
import shutil

class VerificationAgent():
    """验证智能体节点，验证生成的补丁是否有效"""
    
    def __init__(self, config: Dict[str, Any]):
        return
        self.config = config
        self.test_command = config.get("test_command", "pytest")
        self.static_analysis_command = config.get("static_analysis_command", "flake8")
    
    def process(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        print("验证智能体")
        return
        """验证生成的补丁是否有效"""
        patches = input_data.get("patches", {})
        source_code = input_data.get("source_code", {})
        
        if not patches:
            return {
                "verification_passed": False,
                "feedback": ["没有提供补丁"]
            }
        
        # 创建临时目录用于测试
        temp_dir = tempfile.mkdtemp(prefix="patch_verification_")
        try:
            # 复制源代码到临时目录
            self._copy_source_code(source_code, temp_dir)
            
            # 应用补丁
            self._apply_patches(temp_dir, patches)
            
            # 运行静态分析
            static_analysis_passed, static_feedback = self._run_static_analysis(temp_dir)
            
            # 运行测试
            tests_passed, test_feedback = self._run_tests(temp_dir)
            
            # 综合评估
            verification_passed = static_analysis_passed and tests_passed
            feedback = static_feedback + test_feedback
            
            return {
                "verification_passed": verification_passed,
                "feedback": feedback,
                "patches": patches,
                "source_code": source_code,
                "temp_dir": temp_dir if verification_passed else None
            }
        except Exception as e:
            return {
                "verification_passed": False,
                "feedback": [f"验证过程出错: {str(e)}"],
                "patches": patches,
                "source_code": source_code
            }
    
    def _copy_source_code(self, source_code: Dict[str, str], temp_dir: str) -> None:
        """复制源代码到临时目录"""
        for rel_path, code in source_code.items():
            temp_path = os.path.join(temp_dir, rel_path)
            os.makedirs(os.path.dirname(temp_path), exist_ok=True)
            with open(temp_path, 'w') as f:
                f.write(code)
    
    def _apply_patches(self, temp_dir: str, patches: Dict[str, Dict[str, Any]]) -> None:
        """将补丁应用到临时目录中的源代码"""
        for method_name, patch in patches.items():
            # 解析方法名获取文件路径和方法名
            file_path, method_name = method_name.split(':', 1)
            temp_file_path = os.path.join(temp_dir, file_path)
            
            if os.path.exists(temp_file_path):
                with open(temp_file_path, 'r') as f:
                    content = f.read()
                
                # 简单替换整个方法（实际实现需要更复杂的解析）
                new_content = self._replace_method(content, method_name, patch["merged_patch"])
                
                with open(temp_file_path, 'w') as f:
                    f.write(new_content)
    
    def _replace_method(self, content: str, method_name: str, patch_code: str) -> str:
        """替换源代码中的方法（简化实现）"""
        # 实际实现需要解析AST并精确定位方法
        return f"{content}\n\n# 应用的补丁:\n{patch_code}"
    
    def _run_static_analysis(self, source_dir: str) -> Tuple[bool, List[str]]:
        """运行静态分析工具"""
        try:
            result = subprocess.run(
                self.static_analysis_command,
                cwd=source_dir,
                shell=True,
                capture_output=True,
                text=True
            )
            
            passed = result.returncode == 0
            feedback = ["静态分析结果:"]
            if result.stdout:
                feedback.append(result.stdout)
            if result.stderr:
                feedback.append(f"静态分析错误: {result.stderr}")
            
            return passed, feedback
        except Exception as e:
            return False, [f"静态分析运行失败: {str(e)}"]
    
    def _run_tests(self, source_dir: str) -> Tuple[bool, List[str]]:
        """运行测试用例"""
        try:
            result = subprocess.run(
                self.test_command,
                cwd=source_dir,
                shell=True,
                capture_output=True,
                text=True
            )
            
            passed = result.returncode == 0
            feedback = ["测试结果:"]
            if result.stdout:
                feedback.append(result.stdout)
            if result.stderr:
                feedback.append(f"测试错误: {result.stderr}")
            
            return passed, feedback
        except Exception as e:
            return False, [f"测试运行失败: {str(e)}"]    