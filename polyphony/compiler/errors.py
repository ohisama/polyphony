from enum import Enum


class Errors(Enum):
    # type errors
    MUST_BE_X_TYPE = 100
    MISSING_REQUIRED_ARG = 101
    MISSING_REQUIRED_ARG_N = 102
    TAKES_TOOMANY_ARGS = 103
    UNKNOWN_ATTRIBUTE = 104
    INCOMPATIBLE_TYPES = 105
    INCOMPATIBLE_RETURN_TYPE = 106
    INCOMPATIBLE_PARAMETER_TYPE = 107
    LEN_TAKES_ONE_ARG = 108
    LEN_TAKES_SEQ_TYPE = 109
    IS_NOT_CALLABLE = 110

    # semantic errors
    REFERENCED_BEFORE_ASSIGN = 200

    # polyphony language restrictions
    UNSUPPORTED_LETERAL_TYPE = 800
    UNSUPPORTED_OPERAND_FOR = 801
    SEQ_ITEM_MUST_BE_INT = 802
    SEQ_MULTIPLIER_MUST_BE_CONST = 803
    UNSUPPORTED_OPERATOR = 804

    # polyphony library restrictions
    MUDULE_MUST_BE_IN_GLOBAL = 900
    MODULE_FIELD_MUST_ASSIGN_ONLY_ONCE = 901
    MODULE_FIELD_MUST_ASSIGN_IN_CTOR = 902
    UNSUPPORTED_TYPES_IN_FUNC = 903
    WORKER_ARG_MUST_BE_X_TYPE = 904

    def __str__(self):
        return ERROR_MESSAGES[self]


ERROR_MESSAGES = {
    # type errors
    Errors.MUST_BE_X_TYPE: "Type of '{}' must be {}, not {}",
    Errors.MISSING_REQUIRED_ARG: "{}() missing required argument",
    Errors.MISSING_REQUIRED_ARG_N: "{}() missing required argument {}",
    Errors.TAKES_TOOMANY_ARGS: "{}() takes {} positional arguments but {} were given",
    Errors.UNKNOWN_ATTRIBUTE: "Unknown attribute name '{}'",
    Errors.INCOMPATIBLE_TYPES: "'{}' and '{}' are incompatible types",
    Errors.INCOMPATIBLE_RETURN_TYPE: "Type of return value must be {}, not {}",

    Errors.LEN_TAKES_ONE_ARG: "len() takes exactly one argument",
    Errors.LEN_TAKES_SEQ_TYPE: "len() takes sequence type argument",
    Errors.IS_NOT_CALLABLE: "'{}' is not callable",

    # semantic errors
    Errors.REFERENCED_BEFORE_ASSIGN: "local variable '{}' referenced before assignment",

    # polyphony language restrictions
    Errors.UNSUPPORTED_LETERAL_TYPE: "Unsupported literal type {}",
    Errors.UNSUPPORTED_OPERAND_FOR: "Unsupported operand type(s) for {}: {} and {}",
    Errors.SEQ_ITEM_MUST_BE_INT: "Type of sequence item must be int, not {}",
    Errors.SEQ_MULTIPLIER_MUST_BE_CONST: "Type of sequence multiplier must be constant",
    Errors.UNSUPPORTED_OPERATOR: "Unsupported operator {}",

    # polyphony library restrictions
    Errors.MUDULE_MUST_BE_IN_GLOBAL: "@module decorated class must be in the global scope",
    Errors.MODULE_FIELD_MUST_ASSIGN_ONLY_ONCE: "Assignment to a module field can only be done once",
    Errors.MODULE_FIELD_MUST_ASSIGN_IN_CTOR: "Assignment to a module field can only at the constructor",
    Errors.UNSUPPORTED_TYPES_IN_FUNC: "It is not supported to pass the {} type argument to {}()",
    Errors.WORKER_ARG_MUST_BE_X_TYPE: "The type of Worker argument must be an object of Port or constant, not {}"
}
