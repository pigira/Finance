"""The error every service raises when a business rule refuses a form."""


class FormError(Exception):
    """`field` says where the form shows `message`; "form" means above all fields."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message
