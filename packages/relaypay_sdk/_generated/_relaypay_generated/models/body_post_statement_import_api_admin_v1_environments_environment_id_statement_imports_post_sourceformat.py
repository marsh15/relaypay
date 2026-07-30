from enum import Enum

class BodyPostStatementImportApiAdminV1EnvironmentsEnvironmentIdStatementImportsPostSourceformat(str, Enum):
    CSV = "CSV"
    JSON = "JSON"

    def __str__(self) -> str:
        return str(self.value)
