"""Internal safe-workspace file tools."""

from awn.domain.files import FileCreate, FileCreateResult
from awn.infrastructure.filesystem import SafeWorkspaceFiles
from awn.policy.engine import RiskLevel
from awn.tools.contracts import ToolContext, ToolDefinition


def build_file_create_tool(
    files: SafeWorkspaceFiles,
) -> ToolDefinition[FileCreate, FileCreateResult]:
    def create_file(context: ToolContext, command: FileCreate) -> FileCreateResult:
        return files.create_text(
            context.workspace_id,
            command.path,
            command.content,
            tool_call_id=context.tool_call_id,
        )

    return ToolDefinition(
        name="files",
        operation="create",
        summary=(
            "إنشاء ملف UTF-8 جديد داخل جذر مساحة العمل الآمن. "
            "يجب أن يكون path مسارًا نسبيًا ولا يمكن استبدال ملف مختلف موجود."
        ),
        input_model=FileCreate,
        output_model=FileCreateResult,
        risk=RiskLevel.LOW,
        side_effect=True,
        external=False,
        reversible=True,
        required_scopes=("files.write",),
        timeout_seconds=10,
        supports_idempotency=True,
        handler=create_file,
    )
