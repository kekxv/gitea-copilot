"""Review tools definition for OpenAI function calling."""

from typing import List

REVIEW_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "submit_review",
            "description": "提交当前部分的审查结果。每个部分结束时必须调用此工具提交发现的评论和总结。如果还有其他部分待审查，系统会继续下一个部分。",
            "parameters": {
                "type": "object",
                "properties": {
                    "comments": {
                        "type": "array",
                        "description": "行评论列表，每个元素包含 path、body 以及 new_position 或 old_position",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "description": "文件路径"
                                },
                                "new_position": {
                                    "type": "integer",
                                    "description": "对于新增或修改的代码行（标记为 [+]），必须填入其对应的 N 值"
                                },
                                "old_position": {
                                    "type": "integer",
                                    "description": "对于已删除的代码行（标记为 [-]），必须填入其对应的 N 值"
                                },
                                "body": {
                                    "type": "string",
                                    "description": "评论内容。直接指出问题，严禁包含任何行号前缀，严禁在 body 中回显任何发现的 Key 或 Token 原始内容。"
                                }
                            },
                            "required": ["path", "body"]
                        }
                    },
                    "summary": {
                        "type": "string",
                        "description": "审查总结。必须包含明确的结论：如发现问题请详细说明问题类型；若无问题写'LGTM'或'此部分代码无明显缺陷'。"
                    }
                },
                "required": ["comments", "summary"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_file_content",
            "description": "获取仓库中其他文件的内容，用于联合分析。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径"
                    }
                },
                "required": ["path"]
            }
        }
    }
]


def get_review_system_prompt(total_chunks: int = 1) -> str:
    """Get system prompt for review skill with semantics and security focus."""
    chunk_guidance = ""
    if total_chunks > 1:
        chunk_guidance = f"""

### ⚠️ 多部分审查注意：
本 PR 共有 {total_chunks} 个部分需要逐一审查。当前只审查分配给你的部分，完成后调用 submit_review 提交结果。
系统会自动汇总所有部分的审查结果，生成最终报告。"""

    return """你是资深代码专家和首席安全审计员。请对 PR 进行全方位的深度审查。

⚠️ **重要：你必须调用 submit_review 工具提交审查结果，不能直接返回文本！**

""" + chunk_guidance + """

### 核心任务清单：
1. **🛡️ 隐私安全**：严禁硬编码泄露（Token、密码、私钥等）。发现后指出位置，但严禁在评论中回显值。
2. **🔍 语义一致性（重要）**：
   - **注释对齐**：检查代码注释是否准确反映了其实际逻辑。发现误导性或陈旧的注释必须指出。
   - **命名对齐**：检查函数名、变量名是否与其实现的逻辑功能一致。例如：名为 `save_user` 的函数却在删除数据，必须判定为严重缺陷。
3. **⚙️ 逻辑质量**：语法错误、边界条件（如数组越界、空指针）、死循环、资源泄露。

### ⚠️ 必须审查所有文件：
- **禁止跳过任何文件**：即使某个文件看起来简单或只有少量改动，也必须仔细审查
- **逐一审查**：从第一个文件开始，依次审查每个文件，直到最后一个文件
- **禁止提前结束**：不要只审查第一个文件就调用 submit_review，必须审查完所有文件

### 审查流程（必须遵循）：
1. 逐一审查每个文件的每一行代码
2. 发现问题时，记录到 comments 数组中
3. 审查完所有文件后，调用 submit_review 工具提交结果
4. **绝对不要直接返回文本回答，必须使用工具！**

### 总结要求（必须填写）：
- summary 字段是**必填**的，不能省略
- 若发现问题：详细说明问题类型和数量
- 若无问题：必须写 **"LGTM"** 或 **"此部分代码无明显缺陷"**
- 不能留空或不填写 summary

### 评审准则：
- **行号精准**：严格从 diff 的 `[N|+]` 或 `[N|-]` 中提取行号 N。
- **body 脱敏**：严禁回显发现的敏感信息。
- **逐行审查**：不跳过任何代码行，确保全面覆盖。"""


def get_analyze_tools() -> List[dict]:
    """Get tools for analyze skill."""
    return [
        {
            "type": "function",
            "function": {
                "name": "get_file_content",
                "description": "获取仓库中其他文件的内容",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径"}
                    },
                    "required": ["path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "submit_answer",
                "description": "提交最终回答",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "answer": {"type": "string", "description": "回答内容"}
                    },
                    "required": ["answer"]
                }
            }
        }
    ]