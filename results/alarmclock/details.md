# 样例 `alarmclock` 运行输出

**状态:** ❌ 失败
**耗时:** 0.51 秒

---
## 标准输出 (stdout)

无标准输出。

## 标准错误 (stderr)

```
Traceback (most recent call last):
  File "/app/concuFix/main.py", line 123, in <module>
    main()
    ~~~~^^
  File "/app/concuFix/main.py", line 99, in main
    result = initializer.process(state)
  File "/app/concuFix/initializer.py", line 34, in process
    bug_report=self._load_bug_report()
  File "/app/concuFix/initializer.py", line 454, in _load_bug_report
    bug_report.load_race_pairs()
    ~~~~~~~~~~~~~~~~~~~~~~~~~~^^
  File "/app/concuFix/entity.py", line 265, in load_race_pairs
    self._parse_race_from_traces(traces)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^
  File "/app/concuFix/entity.py", line 232, in _parse_race_from_traces
    (t["filename"], self.source_analyzer.find_call_chains(t["filename"], t["line_number"])) for t in
                    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AttributeError: 'JavaSourceAnalyzer' object has no attribute 'find_call_chains'

```
