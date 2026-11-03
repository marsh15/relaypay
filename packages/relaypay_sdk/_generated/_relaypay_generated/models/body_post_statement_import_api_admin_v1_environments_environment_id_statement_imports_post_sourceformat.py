from enum import StrEnum

class BodyPostStatementImportApiAdminV1EnvironmentsEnvironmentIdStatementImportsPostSourceformat(StrEnum):
    CSV = "CSV"
    JSON = "JSON"

    def __str__(self) -> str:
        return str(self.value)
