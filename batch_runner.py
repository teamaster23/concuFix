#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量运行 concuFix 项目的脚本
该脚本会自动识别 benchmarks 文件夹中的样例，并批量运行处理
"""

import os
import json
import subprocess
import sys
import time
from pathlib import Path
import argparse
import logging
from typing import List, Dict, Optional, Tuple

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('batch_run.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

class ConcuFixBatchRunner:
    def __init__(self, project_dir: str = "."):
        self.project_dir = Path(project_dir).resolve()
        self.config_file = self.project_dir / "config.json"
        self.benchmarks_dir = self.project_dir / "benchmarks"
        self.main_script = self.project_dir / "main.py"
        
        # 验证必要文件是否存在
        self._validate_setup()
    
    def _validate_setup(self):
        """验证项目设置"""
        if not self.main_script.exists():
            raise FileNotFoundError(f"主脚本不存在: {self.main_script}")
        
        if not self.benchmarks_dir.exists():
            raise FileNotFoundError(f"benchmarks目录不存在: {self.benchmarks_dir}")
            
        if not self.config_file.exists():
            logger.warning(f"配置文件不存在: {self.config_file}")
            
        logger.info(f"项目根目录: {self.project_dir}")
        logger.info(f"benchmarks目录: {self.benchmarks_dir}")
        logger.info(f"配置文件: {self.config_file}")
    
    def get_benchmarks_samples(self) -> List[Path]:
        """获取benchmarks文件夹中的所有样例"""
        samples = []
        
        # 遍历benchmarks目录，查找样例
        for item in self.benchmarks_dir.iterdir():
            if item.is_dir():
                # 如果是文件夹，认为是一个样例
                samples.append(item)
            elif item.suffix in ['.java', '.c', '.cpp', '.py', '.js']:
                # 如果是代码文件，也认为是一个样例
                samples.append(item)
        
        samples.sort(key=lambda x: x.name)
        return samples
    
    def backup_config(self) -> Optional[Path]:
        """备份原始配置文件"""
        if self.config_file.exists():
            backup_path = self.config_file.with_suffix('.json.backup')
            import shutil
            shutil.copy2(self.config_file, backup_path)
            logger.info(f"已备份配置文件到: {backup_path}")
            return backup_path
        return None
    
    def load_config(self) -> Dict:
        """加载配置文件"""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except json.JSONDecodeError as e:
                logger.error(f"配置文件格式错误: {e}")
                return {}
        return {}
    
    def update_config_for_sample(self, sample_path: Path, base_config: Dict) -> Dict:
        """为特定样例更新配置"""
        config = base_config.copy()
        
        # 针对你的配置文件，直接更新 project_dir 字段
        if 'project_dir' in config:
            config['project_dir'] = str(sample_path)
            logger.info(f"更新 project_dir: {config['project_dir']}")
        else:
            # 如果没有 project_dir 字段，尝试其他可能的字段
            possible_input_fields = [
                'target_file', 'input_path', 'sample_path', 
                'input_file', 'source_file', 'benchmark_path',
                'file_path', 'code_file'
            ]
            
            updated = False
            for field in possible_input_fields:
                if field in config:
                    config[field] = str(sample_path)
                    updated = True
                    logger.info(f"更新配置字段 '{field}': {config[field]}")
                    break
            
            # 如果没有找到已知字段，添加 project_dir 字段
            if not updated:
                config['project_dir'] = str(sample_path)
                logger.info(f"添加 project_dir 字段: {config['project_dir']}")
        
        # 为每个样例创建独立的输出目录
        if 'output_dir' in config:
            # 使用样例名称创建子目录
            sample_output_dir = self.project_dir / "results" / sample_path.stem
            sample_output_dir.mkdir(parents=True, exist_ok=True)
            config['output_dir'] = str(sample_output_dir)
            logger.info(f"为样例 {sample_path.name} 设置输出目录: {sample_output_dir}")
        
        return config
    
    def save_config(self, config: Dict):
        """保存配置文件"""
        with open(self.config_file, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    
    def save_output_to_md(self, output_path: Path, sample_name: str, result_data: Dict):
        """将单个样例的运行结果保存到Markdown文件"""
        content = []
        content.append(f"# 样例 `{sample_name}` 运行输出\n")
        
        status = "✅ 成功" if result_data['success'] else "❌ 失败"
        content.append(f"**状态:** {status}")
        content.append(f"**耗时:** {result_data['duration']:.2f} 秒\n")
        
        content.append("---")
        
        content.append("## 标准输出 (stdout)\n")
        if result_data['stdout']:
            content.append("```")
            content.append(result_data['stdout'])
            content.append("```\n")
        else:
            content.append("无标准输出。\n")

        content.append("## 标准错误 (stderr)\n")
        if result_data['stderr']:
            content.append("```")
            content.append(result_data['stderr'])
            content.append("```\n")
        else:
            content.append("无标准错误输出。\n")
        
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write("\n".join(content))
            logger.info(f"详细输出已保存到: {output_path}")
        except Exception as e:
            logger.error(f"保存输出文件失败: {output_path}, 错误: {e}")

    def run_sample(self, sample_path: Path, timeout: int = 3000) -> Dict:
        """
        运行单个样例并返回结果
        返回: 包含成功状态、stdout、stderr和耗时的字典
        """
        logger.info(f"开始处理样例: {sample_path.name}")
        
        # 添加配置验证
        current_config = self.load_config()
        logger.info(f"当前配置中的关键字段:")
        for key in ['project_dir', 'output_dir']:
            if key in current_config:
                logger.info(f"  {key}: {current_config[key]}")
        
        result_data = {
            'success': False,
            'stdout': '',
            'stderr': '',
            'duration': 0.0
        }
        
        start_time = time.time()
        
        try:
            os.chdir(self.project_dir)
            cmd = [sys.executable, "main.py"]
            
            # 添加更详细的日志
            logger.info(f"执行命令: {' '.join(cmd)}")
            logger.info(f"工作目录: {os.getcwd()}")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding='utf-8'
            )
            
            result_data['stdout'] = result.stdout
            result_data['stderr'] = result.stderr
            
            if result.returncode == 0:
                logger.info(f"样例 {sample_path.name} 处理成功")
                result_data['success'] = True
            else:
                logger.error(f"样例 {sample_path.name} 处理失败，返回码: {result.returncode}")
                if result.stderr:
                    logger.error(f"错误信息: {result.stderr.strip()}")

        except subprocess.TimeoutExpired:
            error_msg = f"样例 {sample_path.name} 处理超时 ({timeout}秒)"
            logger.error(error_msg)
            result_data['stderr'] = error_msg
        except Exception as e:
            error_msg = f"样例 {sample_path.name} 处理异常: {str(e)}"
            logger.error(error_msg)
            result_data['stderr'] = error_msg
        
        end_time = time.time()
        result_data['duration'] = end_time - start_time
        
        # 统一打印耗时
        logger.info(f"样例 {sample_path.name} 处理耗时 {result_data['duration']:.2f}秒")
        
        return result_data

    def run_batch(self, samples: List[Path] = None, timeout: int = 3000, 
                  continue_on_error: bool = True) -> Dict[str, bool]:
        """批量运行样例"""
        if samples is None:
            samples = self.get_benchmarks_samples()
        
        logger.info(f"找到 {len(samples)} 个样例文件夹需要处理")
        
        backup_path = self.backup_config()
        base_config = self.load_config()
        
        results = {}
        successful = 0
        failed = 0
        
        summary_content = []
        summary_content.append("# ConcuFix 批量处理报告\n")
        summary_content.append(f"**处理时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        summary_content.append(f"**总样例数**: {len(samples)}")
        summary_content.append("")
        
        try:
            for i, sample in enumerate(samples, 1):
                logger.info(f"进度: {i}/{len(samples)} - 当前样例: {sample.name}")
                
                updated_config = self.update_config_for_sample(sample, base_config)
                self.save_config(updated_config)
                
                output_dir = None
                if 'output_dir' in updated_config:
                    output_dir = Path(updated_config['output_dir'])

                try:
                    # 运行并获取包含详细输出的结果
                    run_result = self.run_sample(sample, timeout)
                    success = run_result['success']
                    results[sample.name] = success
                    
                    # 保存详细输出到MD文件
                    if output_dir:
                        output_md_file = output_dir / "details.md"
                        self.save_output_to_md(output_md_file, sample.name, run_result)
                    
                    status_icon = "✅" if success else "❌"
                    summary_content.append(f"## {status_icon} {sample.name}")
                    summary_content.append(f"- **状态**: {'成功' if success else '失败'}")
                    summary_content.append(f"- **耗时**: {run_result['duration']:.2f} 秒")
                    # 在主报告中添加详情链接
                    if output_dir:
                        relative_path = os.path.relpath(output_md_file, self.project_dir)
                        summary_content.append(f"- **详细输出**: [点击查看](./{relative_path.replace(os.sep, '/')})")
                    summary_content.append("")
                    
                    if success:
                        successful += 1
                    else:
                        failed += 1
                        if not continue_on_error:
                            logger.info("遇到错误，停止批量处理")
                            break
                            
                except Exception as e:
                    logger.error(f"处理样例 {sample.name} 时发生严重异常: {str(e)}")
                    results[sample.name] = False
                    failed += 1
                    
                    summary_content.append(f"## ❌ {sample.name}")
                    summary_content.append(f"- **状态**: 脚本异常")
                    summary_content.append(f"- **错误**: {str(e)}")
                    summary_content.append("")
                    
                    if not continue_on_error:
                        logger.info("遇到异常，停止批量处理")
                        break
                
                if i < len(samples):
                    time.sleep(1)
        
        finally:
            # 恢复原始配置文件
            if backup_path and backup_path.exists():
                import shutil
                shutil.copy2(backup_path, self.config_file)
                logger.info("已恢复原始配置文件")
        
        summary_content.extend([
            "---",
            "",
            "## 📊 统计信息",
            f"- **成功**: {successful}",
            f"- **失败**: {failed}",
            f"- **总计**: {len(samples)}",
            f"- **成功率**: {(successful/len(samples)*100):.1f}%" if len(samples) > 0 else "- **成功率**: 0%",
            ""
        ])
        
        summary_file = self.project_dir / "batch_summary.md"
        with open(summary_file, 'w', encoding='utf-8') as f:
            f.write("\n".join(summary_content))
        
        logger.info(f"批量处理完成！成功: {successful}, 失败: {failed}, 总计: {len(samples)}")
        logger.info(f"总结报告已保存到: {summary_file}")
        
        return results

def main():
    parser = argparse.ArgumentParser(description='批量运行 concuFix 项目')
    parser.add_argument('--project-dir', '-d', default='.', 
                       help='项目目录路径 (默认: 当前目录)')
    parser.add_argument('--timeout', '-t', type=int, default=3000,
                       help='每个样例的超时时间(秒) (默认: 3000)')
    parser.add_argument('--continue-on-error', '-c', action='store_true',
                       help='遇到错误时继续处理其他样例')
    parser.add_argument('--samples', '-s', nargs='+',
                       help='指定要处理的样例名称 (可选)')
    parser.add_argument('--list-samples', '-l', action='store_true',
                       help='仅列出所有样例，不执行处理')
    
    args = parser.parse_args()
    
    try:
        runner = ConcuFixBatchRunner(args.project_dir)
        
        if args.list_samples:
            samples = runner.get_benchmarks_samples()
            print(f"在 {runner.benchmarks_dir} 中找到 {len(samples)} 个样例:")
            for sample in samples:
                print(f"  - {sample.name}")
            return
        
        all_samples = runner.get_benchmarks_samples()
        
        if args.samples:
            specified_samples = []
            for sample_name in args.samples:
                found = False
                for sample in all_samples:
                    if sample.name == sample_name:
                        specified_samples.append(sample)
                        found = True
                        break
                if not found:
                    logger.warning(f"找不到样例: {sample_name}")
            samples_to_run = specified_samples
        else:
            samples_to_run = all_samples
        
        if not samples_to_run:
            logger.error("没有找到要处理的样例")
            return
        
        results = runner.run_batch(
            samples_to_run, 
            timeout=args.timeout,
            continue_on_error=args.continue_on_error
        )
        
        print("\n" + "="*50)
        print("处理结果汇总:")
        print("="*50)
        for sample_name, success in results.items():
            status = "✓ 成功" if success else "✗ 失败"
            print(f"{sample_name:<30} {status}")
        
    except Exception as e:
        logger.error(f"运行失败: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()