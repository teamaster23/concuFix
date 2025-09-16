from typing import Dict, Any
import os
import shutil

class PatchApplier():
    """补丁应用器节点，将验证通过的补丁应用到原始代码"""
    
    def __init__(self, config: Dict[str, Any]):
        return
        self.config = config
        self.source_dir = config.get("source_dir", "./source_code")
    
    def process(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        print("我来了")
        return
        """将验证通过的补丁应用到原始代码"""
        verification_passed = input_data.get("verification_passed", False)
        patches = input_data.get("patches", {})
        source_code = input_data.get("source_code", {})
        temp_dir = input_data.get("temp_dir")
        
        if not verification_passed:
            return {
                "applied": False,
                "message": "补丁验证失败，未应用任何修改",
                "feedback": input_data.get("feedback", [])
            }
        
        if not temp_dir or not os.path.exists(temp_dir):
            return {
                "applied": False,
                "message": "没有有效的临时目录用于应用补丁"
            }
        
        try:
            # 将临时目录中的修改应用回原始代码
            self._apply_changes_back(temp_dir, self.source_dir)
            
            return {
                "applied": True,
                "message": "补丁已成功应用到原始代码",
                "patches_applied": patches,
                "source_code": source_code
            }
        except Exception as e:
            return {
                "applied": False,
                "message": f"应用补丁时出错: {str(e)}",
                "patches": patches,
                "source_code": source_code
            }
        finally:
            # 清理临时目录
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
    
    def _apply_changes_back(self, temp_dir: str, source_dir: str) -> None:
        """将临时目录中的修改应用回原始代码"""
        for root, _, files in os.walk(temp_dir):
            for file in files:
                temp_path = os.path.join(root, file)
                rel_path = os.path.relpath(temp_path, temp_dir)
                source_path = os.path.join(source_dir, rel_path)
                
                # 创建目标目录（如果不存在）
                os.makedirs(os.path.dirname(source_path), exist_ok=True)
                
                # 复制文件
                shutil.copy2(temp_path, source_path)    