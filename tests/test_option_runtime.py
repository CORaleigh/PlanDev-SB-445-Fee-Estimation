import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPTS_DIR / "option_runtime.py"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

SPEC = importlib.util.spec_from_file_location("option_runtime", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def expression_condition(*conditions):
    return {
        "kind": "expression",
        "sourceRule": {
            "isOrGroup": False,
            "childConditions": [
                {
                    "operandValue": field,
                    "operandFriendlyName": label,
                    "operatorID": 1,
                    "compareExpression": {"value": value},
                }
                for field, label, value in conditions
            ],
        },
    }


def fixed_fee(fee_id, name, amount):
    return {
        "feeTemplateFeeId": fee_id,
        "name": name,
        "section": "configured",
        "applicability": {
            "status": "configured",
            "conditions": [],
        },
        "calculation": {
            "mode": "fixed",
            "fixedAmount": amount,
            "rule": {},
        },
    }


def input_fee(
    fee_id,
    name,
    rate,
    inputs,
    *,
    section="input-required",
    conditions=None,
    minimum=0,
    rounding_type=1,
    rounding_value=0,
):
    return {
        "feeTemplateFeeId": fee_id,
        "name": name,
        "section": section,
        "applicability": {
            "status": (
                "conditional" if section == "conditional" else "configured"
            ),
            "conditions": list(conditions or []),
        },
        "calculation": {
            "mode": "input",
            "rule": {
                "amount": rate,
                "minimum": minimum,
                "maximum": 0,
                "roundingType": rounding_type,
                "roundingValue": rounding_value,
                "inputs": inputs,
            },
        },
    }


def custom_input(key, label):
    return {
        "inputKey": key,
        "label": label,
        "source": "custom-field",
        "variable": True,
    }


def fee_input(key, label):
    return {
        "inputKey": key,
        "label": label,
        "source": "fee",
        "variable": True,
    }


def row_archetype_document():
    """
    Compact regression fixture derived from the validated residential ROW rule
    shapes. It is deliberately a generic catalog document, not production
    special-case code.
    """
    site = fixed_fee("SITE", "ROW - Site Final", 124)
    new_driveway = input_fee(
        "NEW",
        "ROW - New Driveway Fee",
        141,
        [custom_input("NumberofNewDriveways", "# of New Driveways")],
    )
    sidewalk = input_fee(
        "SIDEWALK",
        "ROW - Sidewalk Installation",
        0.4,
        [
            custom_input(
                "SidewalkLinearFeet",
                "New sidewalk installation in the ROW - Linear Feet",
            )
        ],
        section="conditional",
        conditions=[
            expression_condition(
                (
                    "SidewalkInstallation",
                    "Sidewalk Installation",
                    True,
                ),
                (
                    "SiteFinalOnly",
                    "Site Final Only",
                    False,
                ),
            )
        ],
        minimum=141,
    )
    technology = input_fee(
        "TECH",
        "MISC - Technology Surcharge",
        4,
        [
            fee_input("SITE", "ROW - Site Final"),
            fee_input("NEW", "ROW - New Driveway Fee"),
            fee_input("SIDEWALK", "ROW - Sidewalk Installation"),
        ],
        rounding_type=2,
        rounding_value=1,
    )
    return {
        "catalogVersion": "test-r1",
        "configuration": {"asOfDate": "2026-09-15"},
        "option": {
            "optionKey": "ROW-LIKE",
            "permitType": "Generic Complex Permit",
            "workClass": "Generic Complex Work Class",
        },
        "fees": [site, new_driveway, sidewalk, technology],
        "requiredInputs": [],
        "disclosures": [],
    }


class OptionRuntimeTests(unittest.TestCase):
    def test_compiles_user_controls_from_catalog_rules(self):
        schema = MODULE.compile_option_schema(row_archetype_document())
        fields = {
            item["key"]: item
            for item in schema["fields"]
        }

        self.assertEqual(
            set(fields),
            {
                "NumberofNewDriveways",
                "SidewalkLinearFeet",
                "SidewalkInstallation",
                "SiteFinalOnly",
            },
        )
        self.assertEqual(
            fields["NumberofNewDriveways"]["valueType"],
            "number",
        )
        self.assertEqual(
            fields["SidewalkLinearFeet"]["valueType"],
            "number",
        )
        self.assertEqual(
            fields["SidewalkInstallation"]["valueType"],
            "boolean",
        )
        self.assertEqual(
            fields["SiteFinalOnly"]["valueType"],
            "boolean",
        )
        self.assertEqual(
            fields["SidewalkInstallation"]["choices"],
            [
                {"value": False, "label": "No"},
                {"value": True, "label": "Yes"},
            ],
        )
        self.assertEqual(
            fields["SiteFinalOnly"]["choices"],
            [
                {"value": False, "label": "No"},
                {"value": True, "label": "Yes"},
            ],
        )
        self.assertEqual(schema["compileBlockers"], [])

        # Fee-derived technology inputs are dependencies, not user controls.
        self.assertNotIn("SITE", fields)
        self.assertNotIn("NEW", fields)
        self.assertNotIn("SIDEWALK", fields)

    def test_review_only_sections_do_not_count_as_runtime_blockers(self):
        document = row_archetype_document()
        document["fees"].append(
            {
                "feeTemplateFeeId": "MANUAL-CHILD",
                "name": "Manual Child Fee",
                "section": "manual-review",
                "sourceTemplate": "Child Template",
                "templateRelationship": "Child",
                "applicability": {
                    "status": "manual-review",
                    "conditions": [
                        {
                            "kind": "child-template",
                            "supported": False,
                        }
                    ],
                },
                "calculation": {
                    "mode": "manual",
                    "rule": {},
                },
            }
        )

        schema = MODULE.compile_option_schema(document)

        self.assertTrue(schema["runtimeSchemaReady"])
        self.assertEqual(schema["blockingCompileBlockers"], [])
        self.assertEqual(
            len(schema["reviewCompileDiagnostics"]),
            1,
        )
        blocker = schema["reviewCompileDiagnostics"][0]
        self.assertEqual(blocker["severity"], "review-only")
        self.assertEqual(blocker["sourceTemplate"], "Child Template")
        self.assertEqual(blocker["templateRelationship"], "Child")

    def test_event_contingent_condition_does_not_block_runtime_schema(self):
        document = row_archetype_document()
        document["fees"].append(
            {
                "feeTemplateFeeId": "INSPECTION-EVENT",
                "name": "Inspection Event Fee",
                "section": "event-contingent",
                "applicability": {
                    "status": "event-contingent",
                    "conditions": [
                        {
                            "kind": "inspection",
                            "description": (
                                "Fee is contingent on an inspection event."
                            ),
                            "supported": False,
                        }
                    ],
                },
                "calculation": {
                    "mode": "fixed",
                    "fixedAmount": 50,
                    "rule": {},
                },
            }
        )

        schema = MODULE.compile_option_schema(document)

        self.assertTrue(schema["runtimeSchemaReady"])
        self.assertEqual(schema["blockingCompileBlockers"], [])
        self.assertEqual(
            len(schema["reviewCompileDiagnostics"]),
            1,
        )
        blocker = schema["reviewCompileDiagnostics"][0]
        self.assertEqual(blocker["feeTemplateFeeId"], "INSPECTION-EVENT")
        self.assertEqual(blocker["conditionKind"], "inspection")
        self.assertEqual(blocker["severity"], "review-only")

    def test_operator_2_compiles_and_evaluates_as_not_equal(self):
        document = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "NOT-EQUAL",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [
                {
                    "feeTemplateFeeId": "NOT-EQUAL-FEE",
                    "name": "Not Equal Conditional Fee",
                    "section": "conditional",
                    "applicability": {
                        "status": "conditional",
                        "conditions": [
                            {
                                "kind": "expression",
                                "sourceRule": {
                                    "isOrGroup": False,
                                    "childConditions": [
                                        {
                                            "operandValue": "PermitWorkClassID",
                                            "operandFriendlyName": "Permit Work Class ID",
                                            "operatorID": 2,
                                            "compareExpression": {
                                                "value": "EXCLUDED"
                                            },
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": 75,
                        "rule": {},
                    },
                }
            ],
            "requiredInputs": [],
            "disclosures": [],
        }

        schema = MODULE.compile_option_schema(document)
        fields = {item["key"]: item for item in schema["fields"]}
        self.assertTrue(schema["runtimeSchemaReady"])
        self.assertEqual(
            fields["PermitWorkClassID"]["valueType"],
            "choice",
        )

        included = MODULE.evaluate_option(
            document,
            {"PermitWorkClassID": "OTHER"},
        )
        excluded = MODULE.evaluate_option(
            document,
            {"PermitWorkClassID": "EXCLUDED"},
        )

        self.assertEqual(included["report"]["estimatedTotal"], 75.0)
        self.assertEqual(excluded["report"]["estimatedTotal"], 0.0)

    def test_choice_condition_uses_preserved_friendly_label(self):
        guid = "021FD65F-EA7C-48BF-84E9-383E5ADD28BE"
        document = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "LABELED-CHOICE",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [
                {
                    "feeTemplateFeeId": "LABELED-FEE",
                    "name": "Labeled Conditional Fee",
                    "section": "conditional",
                    "applicability": {
                        "status": "conditional",
                        "conditions": [
                            {
                                "kind": "expression",
                                "sourceRule": {
                                    "isOrGroup": False,
                                    "childConditions": [
                                        {
                                            "operandValue": "PermitWorkClassID",
                                            "operandFriendlyName": "Permit Work Class",
                                            "operatorID": 1,
                                            "compareExpression": {
                                                "value": guid,
                                                "label": "New Construction",
                                                "labelSource": "PMPERMITWORKCLASS.NAME",
                                            },
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": 75,
                        "rule": {},
                    },
                }
            ],
            "requiredInputs": [],
            "disclosures": [],
        }

        schema = MODULE.compile_option_schema(document)
        field = schema["fields"][0]

        self.assertEqual(
            field["choices"],
            [{"value": guid, "label": "New Construction"}],
        )
        included = MODULE.evaluate_option(
            document,
            {"PermitWorkClassID": guid},
        )
        self.assertEqual(included["report"]["estimatedTotal"], 75.0)

    def test_application_date_is_system_resolved_not_user_input(self):
        def document(as_of):
            return {
                "catalogVersion": "test-r1",
                "configuration": {"asOfDate": as_of},
                "option": {
                    "optionKey": "SYSTEM-APPLICATION-DATE",
                    "permitType": "Generic Permit",
                    "workClass": "Generic Work",
                },
                "fees": [
                    {
                        "feeTemplateFeeId": "DATE-FEE",
                        "name": "Date Conditional Fee",
                        "section": "conditional",
                        "applicability": {
                            "status": "conditional",
                            "conditions": [
                                {
                                    "kind": "expression",
                                    "sourceRule": {
                                        "isOrGroup": False,
                                        "childConditions": [
                                            {
                                                "className": (
                                                    "EnerGovBusiness."
                                                    "PermitManagement.Permit"
                                                ),
                                                "operandTypeID": 2,
                                                "operandDataTypeID": 2,
                                                "operandValue": "ApplicationDate",
                                                "operandFriendlyName": (
                                                    "Permit Application Date"
                                                ),
                                                "operatorID": 1,
                                                "compareExpression": {
                                                    "value": (
                                                        "2021-04-05T00:00:00"
                                                    )
                                                },
                                            }
                                        ],
                                    },
                                }
                            ],
                        },
                        "calculation": {
                            "mode": "fixed",
                            "fixedAmount": 50,
                            "rule": {},
                        },
                    }
                ],
                "requiredInputs": [],
                "disclosures": [],
            }

        current = document("2026-09-15")
        historical = document("2021-04-05")

        schema = MODULE.compile_option_schema(current)
        self.assertEqual(schema["fields"], [])
        self.assertTrue(schema["runtimeSchemaReady"])

        current_result = MODULE.evaluate_option(current, {})
        historical_result = MODULE.evaluate_option(historical, {})

        self.assertEqual(
            current_result["systemApplicabilityValues"],
            {"ApplicationDate": "2026-09-15T00:00:00"},
        )
        self.assertEqual(
            historical_result["systemApplicabilityValues"],
            {"ApplicationDate": "2021-04-05T00:00:00"},
        )
        self.assertEqual(current_result["report"]["estimatedTotal"], 0.0)
        self.assertEqual(historical_result["report"]["estimatedTotal"], 50.0)

    def test_selected_work_class_is_system_resolved_not_user_input(self):
        def document(work_class_id):
            return {
                "catalogVersion": "test-r1",
                "configuration": {"asOfDate": "2026-09-15"},
                "option": {
                    "optionKey": "SYSTEM-WORK-CLASS",
                    "permitTypeId": "PERMIT-TYPE-1",
                    "permitType": "Generic Permit",
                    "workClassId": work_class_id,
                    "workClass": "Generic Work",
                },
                "fees": [
                    {
                        "feeTemplateFeeId": "WORK-CLASS-FEE",
                        "name": "Work Class Conditional Fee",
                        "section": "conditional",
                        "applicability": {
                            "status": "conditional",
                            "conditions": [
                                {
                                    "kind": "expression",
                                    "sourceRule": {
                                        "isOrGroup": False,
                                        "childConditions": [
                                            {
                                                "className": (
                                                    "EnerGovBusiness."
                                                    "PermitManagement.Permit"
                                                ),
                                                "operandTypeID": 2,
                                                "operandDataTypeID": 3,
                                                "operandValue": "PermitWorkClassID",
                                                "operandFriendlyName": (
                                                    "Permit WorkClass ID"
                                                ),
                                                "operatorID": 1,
                                                "compareExpression": {
                                                    "value": "WORK-CLASS-A"
                                                },
                                            }
                                        ],
                                    },
                                }
                            ],
                        },
                        "calculation": {
                            "mode": "fixed",
                            "fixedAmount": 75,
                            "rule": {},
                        },
                    }
                ],
                "requiredInputs": [],
                "disclosures": [],
            }

        included = document("WORK-CLASS-A")
        excluded = document("WORK-CLASS-B")

        schema = MODULE.compile_option_schema(included)
        self.assertEqual(schema["fields"], [])
        self.assertTrue(schema["runtimeSchemaReady"])

        included_result = MODULE.evaluate_option(included, {})
        excluded_result = MODULE.evaluate_option(excluded, {})

        self.assertEqual(
            included_result["systemApplicabilityValues"]["PermitWorkClassID"],
            "WORK-CLASS-A",
        )
        self.assertEqual(included_result["report"]["estimatedTotal"], 75.0)
        self.assertEqual(excluded_result["report"]["estimatedTotal"], 0.0)

    def test_review_and_event_only_fields_do_not_compile_as_user_inputs(self):
        document = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "NON-INPUT-REVIEW-FIELDS",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [
                {
                    "feeTemplateFeeId": "EVENT-FEE",
                    "name": "Reinspection",
                    "section": "event-contingent",
                    "applicability": {
                        "status": "event-contingent",
                        "conditions": [
                            {
                                "kind": "expression",
                                "sourceRule": {
                                    "isOrGroup": False,
                                    "childConditions": [
                                        {
                                            "operandValue": "InspectionStatusID",
                                            "operandFriendlyName": "Inspection Status",
                                            "operatorID": 1,
                                            "compareExpression": {
                                                "value": "STATUS-A"
                                            },
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": 50,
                        "rule": {},
                    },
                },
                {
                    "feeTemplateFeeId": "MANUAL-FEE",
                    "name": "Manual Workflow Fee",
                    "section": "manual-review",
                    "applicability": {
                        "status": "manual-review",
                        "conditions": [],
                    },
                    "calculation": {
                        "mode": "manual",
                        "rule": {
                            "inputs": [
                                {
                                    "inputKey": "WorkflowValue",
                                    "label": "Workflow Value",
                                    "source": "custom-field",
                                }
                            ]
                        },
                    },
                },
            ],
            "requiredInputs": [],
            "disclosures": [],
        }

        schema = MODULE.compile_option_schema(document)

        self.assertEqual(schema["fields"], [])
        self.assertTrue(schema["runtimeSchemaReady"])

    def test_operator_3_compiles_as_numeric_control_and_evaluates(self):
        document = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "GREATER-THAN",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [
                {
                    "feeTemplateFeeId": "GT-FEE",
                    "name": "Greater Than Conditional Fee",
                    "section": "conditional",
                    "applicability": {
                        "status": "conditional",
                        "conditions": [
                            {
                                "kind": "expression",
                                "sourceRule": {
                                    "isOrGroup": False,
                                    "childConditions": [
                                        {
                                            "operandValue": "Quantity",
                                            "operandFriendlyName": "Quantity",
                                            "operatorID": 3,
                                            "compareExpression": {"value": 0},
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": 50,
                        "rule": {},
                    },
                }
            ],
            "requiredInputs": [],
            "disclosures": [],
        }

        schema = MODULE.compile_option_schema(document)
        fields = {item["key"]: item for item in schema["fields"]}

        self.assertTrue(schema["runtimeSchemaReady"])
        self.assertEqual(fields["Quantity"]["valueType"], "number")
        self.assertEqual(fields["Quantity"]["choices"], [])

        included = MODULE.evaluate_option(document, {"Quantity": 2})
        excluded = MODULE.evaluate_option(document, {"Quantity": 0})

        self.assertEqual(included["report"]["estimatedTotal"], 50.0)
        self.assertEqual(excluded["report"]["estimatedTotal"], 0.0)

    def test_numeric_plus_isblank_compiles_as_nullable_number(self):
        document = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "NULLABLE-NUMBER",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [
                {
                    "feeTemplateFeeId": "POSITIVE-FEE",
                    "name": "Positive Acres Fee",
                    "section": "conditional",
                    "applicability": {
                        "status": "conditional",
                        "conditions": [
                            {
                                "kind": "expression",
                                "sourceRule": {
                                    "isOrGroup": False,
                                    "childConditions": [
                                        {
                                            "operandValue": "DisturbedAcres",
                                            "operandFriendlyName": "Disturbed Acres",
                                            "operatorID": 3,
                                            "compareExpression": {"value": 0},
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                    "calculation": {
                        "mode": "input",
                        "rule": {
                            "amount": 10,
                            "inputs": [
                                {
                                    "inputKey": "DisturbedAcres",
                                    "label": "Disturbed Acres",
                                    "source": "custom-field",
                                }
                            ],
                        },
                    },
                },
                {
                    "feeTemplateFeeId": "BLANK-FEE",
                    "name": "Blank Acres Fee",
                    "section": "conditional",
                    "applicability": {
                        "status": "conditional",
                        "conditions": [
                            {
                                "kind": "expression",
                                "sourceRule": {
                                    "isOrGroup": False,
                                    "childConditions": [
                                        {
                                            "operandValue": "DisturbedAcres",
                                            "operandFriendlyName": "Disturbed Acres",
                                            "operatorID": 7,
                                            "compareExpression": {"value": 0},
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": 25,
                        "rule": {},
                    },
                },
            ],
            "requiredInputs": [],
            "disclosures": [],
        }

        schema = MODULE.compile_option_schema(document)
        field = {item["key"]: item for item in schema["fields"]}[
            "DisturbedAcres"
        ]

        self.assertTrue(schema["runtimeSchemaReady"])
        self.assertEqual(field["valueType"], "nullable-number")
        self.assertEqual(field["choices"], [])

        blank_values = MODULE.coerce_option_values(
            schema,
            {"DisturbedAcres": ""},
        )
        self.assertIn("DisturbedAcres", blank_values)
        self.assertIsNone(blank_values["DisturbedAcres"])

        blank = MODULE.evaluate_option(document, blank_values)
        numeric = MODULE.evaluate_option(
            document,
            {"DisturbedAcres": 2},
        )

        self.assertEqual(blank["report"]["estimatedTotal"], 25.0)
        self.assertEqual(numeric["report"]["estimatedTotal"], 20.0)

    def test_operator_7_compiles_as_presence_control_and_evaluates_blank(self):
        document = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "BLANK",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [
                {
                    "feeTemplateFeeId": "BLANK-FEE",
                    "name": "Blank Conditional Fee",
                    "section": "conditional",
                    "applicability": {
                        "status": "conditional",
                        "conditions": [
                            {
                                "kind": "expression",
                                "sourceRule": {
                                    "isOrGroup": False,
                                    "childConditions": [
                                        {
                                            "operandValue": "DisturbedAcres",
                                            "operandFriendlyName": "Disturbed Acres",
                                            "operatorID": 7,
                                            "compareExpression": {"value": 0},
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": 50,
                        "rule": {},
                    },
                }
            ],
            "requiredInputs": [],
            "disclosures": [],
        }

        schema = MODULE.compile_option_schema(document)
        fields = {item["key"]: item for item in schema["fields"]}

        self.assertTrue(schema["runtimeSchemaReady"])
        self.assertEqual(fields["DisturbedAcres"]["valueType"], "presence")

        blank = MODULE.evaluate_option(document, {"DisturbedAcres": False})
        populated = MODULE.evaluate_option(document, {"DisturbedAcres": True})

        self.assertEqual(blank["report"]["estimatedTotal"], 50.0)
        self.assertEqual(populated["report"]["estimatedTotal"], 0.0)

    def test_operator_8_compiles_as_presence_control(self):
        document = row_archetype_document()
        document["fees"].append(
            {
                "feeTemplateFeeId": "PRESENT",
                "name": "Presence Conditional Fee",
                "section": "conditional",
                "applicability": {
                    "status": "conditional",
                    "conditions": [
                        {
                            "kind": "expression",
                            "sourceRule": {
                                "isOrGroup": False,
                                "childConditions": [
                                    {
                                        "operandValue": "DomesticStubSize",
                                        "operandFriendlyName": "Domestic Stub Size",
                                        "operatorID": 8,
                                        "compareExpression": {
                                            "value": "configured-guid"
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                },
                "calculation": {
                    "mode": "fixed",
                    "fixedAmount": 50,
                    "rule": {},
                },
            }
        )

        schema = MODULE.compile_option_schema(document)
        fields = {item["key"]: item for item in schema["fields"]}

        self.assertTrue(schema["runtimeSchemaReady"])
        self.assertEqual(
            fields["DomesticStubSize"]["valueType"],
            "presence",
        )
        self.assertEqual(
            fields["DomesticStubSize"]["choices"],
            [
                {"value": False, "label": "No"},
                {"value": True, "label": "Yes"},
            ],
        )
        values = MODULE.coerce_option_values(
            schema,
            {"DomesticStubSize": "yes"},
        )
        self.assertTrue(values["DomesticStubSize"])
        unanswered = MODULE.coerce_option_values(
            schema,
            {"DomesticStubSize": ""},
        )
        self.assertNotIn("DomesticStubSize", unanswered)

    def test_operator_8_presence_condition_evaluates_generic_fee(self):
        document = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "PRESENCE",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [
                {
                    "feeTemplateFeeId": "PRESENT",
                    "name": "Presence Conditional Fee",
                    "section": "conditional",
                    "applicability": {
                        "status": "conditional",
                        "conditions": [
                            {
                                "kind": "expression",
                                "sourceRule": {
                                    "isOrGroup": False,
                                    "childConditions": [
                                        {
                                            "operandValue": "DomesticStubSize",
                                            "operandFriendlyName": "Domestic Stub Size",
                                            "operatorID": 8,
                                            "compareExpression": {
                                                "value": "configured-guid"
                                            },
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": 50,
                        "rule": {},
                    },
                }
            ],
            "requiredInputs": [],
            "disclosures": [],
        }

        included = MODULE.evaluate_option(
            document,
            {"DomesticStubSize": True},
        )
        excluded = MODULE.evaluate_option(
            document,
            {"DomesticStubSize": False},
        )

        self.assertEqual(included["report"]["estimatedTotal"], 50.0)
        self.assertEqual(excluded["report"]["estimatedTotal"], 0.0)

    def test_compile_blocker_preserves_fee_and_operator_context(self):
        document = row_archetype_document()
        document["fees"].append(
            {
                "feeTemplateFeeId": "UNSUPPORTED",
                "name": "Unsupported Conditional Fee",
                "section": "conditional",
                "applicability": {
                    "status": "conditional",
                    "conditions": [
                        {
                            "kind": "expression",
                            "sourceRule": {
                                "isOrGroup": False,
                                "childConditions": [
                                    {
                                        "operandValue": "SomeField",
                                        "operandFriendlyName": "Some Field",
                                        "operatorID": 99,
                                        "compareExpression": {"value": 10},
                                    }
                                ],
                            },
                        }
                    ],
                },
                "calculation": {
                    "mode": "fixed",
                    "fixedAmount": 50,
                    "rule": {},
                },
            }
        )

        schema = MODULE.compile_option_schema(document)

        blocker = schema["compileBlockers"][0]
        self.assertEqual(blocker["feeTemplateFeeId"], "UNSUPPORTED")
        self.assertEqual(blocker["feeName"], "Unsupported Conditional Fee")
        self.assertEqual(blocker["section"], "conditional")
        self.assertEqual(blocker["operatorID"], 99)
        self.assertEqual(blocker["operandValue"], "SomeField")
        self.assertEqual(blocker["comparisonValue"], 10)
        self.assertEqual(blocker["severity"], "blocking")
        self.assertFalse(schema["runtimeSchemaReady"])
        self.assertEqual(
            schema["blockingCompileBlockers"][0]["feeTemplateFeeId"],
            "UNSUPPORTED",
        )

    def test_generic_complex_option_matches_423_observed_archetype(self):
        runtime = MODULE.evaluate_option(
            row_archetype_document(),
            {
                "NumberofNewDriveways": 1,
                "SidewalkLinearFeet": 100,
                "SidewalkInstallation": True,
                "SiteFinalOnly": False,
            },
        )
        report = runtime["report"]

        self.assertEqual(report["estimateStatus"], "automatic-estimate-complete")
        self.assertEqual(report["estimatedTotal"], 423.0)
        amounts = {
            item["name"]: item["amount"]
            for item in report["regularPermitFees"]
        }
        self.assertEqual(amounts["ROW - Site Final"], 124.0)
        self.assertEqual(amounts["ROW - New Driveway Fee"], 141.0)
        self.assertEqual(amounts["ROW - Sidewalk Installation"], 141.0)
        self.assertEqual(amounts["MISC - Technology Surcharge"], 17.0)

    def test_generic_complex_option_matches_276_without_sidewalk(self):
        runtime = MODULE.evaluate_option(
            row_archetype_document(),
            {
                "NumberofNewDriveways": 1,
                "SidewalkInstallation": False,
                "SiteFinalOnly": False,
            },
        )
        report = runtime["report"]

        self.assertEqual(report["estimateStatus"], "automatic-estimate-complete")
        self.assertEqual(report["estimatedTotal"], 276.0)
        names = {
            item["name"]
            for item in report["regularPermitFees"]
        }
        self.assertNotIn("ROW - Sidewalk Installation", names)

    def test_unvalidated_formula_blocks_downstream_fee_but_preserves_known_subtotal(self):
        document = row_archetype_document()
        document["fees"] = [
            fee
            for fee in document["fees"]
            if fee["feeTemplateFeeId"] != "TECH"
        ]
        formula = {
            "feeTemplateFeeId": "THRESHOLD-FORMULA",
            "name": "ROW - Threshold Formula Fee",
            "section": "conditional",
            "applicability": {
                "status": "conditional",
                "conditions": [
                    {
                        "kind": "expression",
                        "sourceRule": {
                            "isOrGroup": False,
                            "childConditions": [
                                {
                                    "operandValue": "SiteFinalOnly",
                                    "operandFriendlyName": "Site Final Only",
                                    "operatorID": 1,
                                    "compareExpression": {"value": False},
                                },
                                {
                                    "operandValue": "SidewalkLinearFeet",
                                    "operandFriendlyName": "Sidewalk Linear Feet",
                                    "operatorID": 3,
                                    "compareExpression": {"value": 340},
                                },
                            ],
                        },
                    }
                ],
            },
            "calculation": {
                "mode": "formula",
                "rule": {
                    "amount": 0.33,
                    "minimum": 0,
                    "maximum": 0,
                    "roundingType": 1,
                    "roundingValue": 0,
                    "inputs": [
                        custom_input("SidewalkLinearFeet", "Sidewalk Linear Feet")
                    ],
                    "expression": {"expressionType": 2},
                },
            },
        }
        technology = input_fee(
            "THRESHOLD-TECH",
            "Technology Fee",
            4,
            [fee_input("THRESHOLD-FORMULA", "ROW - Threshold Formula Fee")],
            rounding_type=2,
            rounding_value=1,
        )
        document["fees"].extend([formula, technology])

        runtime = MODULE.evaluate_option(
            document,
            {
                "NumberofNewDriveways": 0,
                "SidewalkLinearFeet": 341,
                "SidewalkInstallation": True,
                "SiteFinalOnly": False,
            },
        )
        report = runtime["report"]

        self.assertEqual(report["estimateStatus"], "staff-review-required")
        self.assertEqual(report["estimatedTotal"], 265.0)
        self.assertEqual(report["unresolvedApplicabilityFees"], [])
        blocked = {
            item["feeTemplateFeeId"]: item["validationCode"]
            for item in report["blockedFees"]
        }
        self.assertEqual(
            blocked,
            {
                "THRESHOLD-FORMULA": "FORMULA_CALCULATION_UNVALIDATED",
                "THRESHOLD-TECH": "FORMULA_CALCULATION_UNVALIDATED",
            },
        )

    def test_later_event_dependency_does_not_block_initial_estimate(self):
        document = row_archetype_document()
        document["fees"].extend(
            [
                input_fee(
                    "LATER",
                    "DSENG - Voiding Permit Fee - Engineering (ROW)",
                    100,
                    [fee_input("SITE", "ROW - Site Final")],
                    section="conditional",
                    conditions=[
                        expression_condition(
                            (
                                "WorkflowAction",
                                "Workflow Action",
                                "Void Permit",
                            )
                        )
                    ],
                ),
                input_fee(
                    "LATER-TECH",
                    "Technology Fee",
                    4,
                    [
                        fee_input(
                            "LATER",
                            "DSENG - Voiding Permit Fee - Engineering (ROW)",
                        )
                    ],
                    rounding_type=2,
                    rounding_value=1,
                ),
            ]
        )

        runtime = MODULE.evaluate_option(
            document,
            {
                "NumberofNewDriveways": 1,
                "SidewalkInstallation": False,
                "SiteFinalOnly": False,
            },
        )
        report = runtime["report"]

        self.assertEqual(report["estimateStatus"], "automatic-estimate-complete")
        self.assertEqual(report["estimatedTotal"], 276.0)
        self.assertEqual(report["blockedFees"], [])
        self.assertEqual(report["unresolvedApplicabilityFees"], [])
        self.assertIn(
            "LATER",
            {
                item["feeTemplateFeeId"]
                for item in report["excludedFees"]
            },
        )
        self.assertEqual(
            next(
                item["amount"]
                for item in report["calculatedFees"]
                if item["feeTemplateFeeId"] == "LATER-TECH"
            ),
            0.0,
        )

    def test_conditional_calculation_input_is_not_required_when_fee_is_excluded(self):
        runtime = MODULE.evaluate_option(
            row_archetype_document(),
            {
                "NumberofNewDriveways": 1,
                "SidewalkInstallation": True,
                "SiteFinalOnly": True,
            },
        )

        self.assertEqual(
            runtime["report"]["estimatedTotal"],
            276.0,
        )

    def test_coerce_option_values_from_form_strings(self):
        schema = MODULE.compile_option_schema(row_archetype_document())

        values = MODULE.coerce_option_values(
            schema,
            {
                "NumberofNewDriveways": "2",
                "SidewalkLinearFeet": "141.5",
                "SidewalkInstallation": "yes",
                "SiteFinalOnly": "false",
            },
        )

        self.assertEqual(values["NumberofNewDriveways"], 2)
        self.assertEqual(values["SidewalkLinearFeet"], 141.5)
        self.assertTrue(values["SidewalkInstallation"])
        self.assertFalse(values["SiteFinalOnly"])

    def test_coerce_option_values_omits_blank_fields(self):
        schema = MODULE.compile_option_schema(row_archetype_document())

        values = MODULE.coerce_option_values(
            schema,
            {
                "NumberofNewDriveways": "",
                "SidewalkInstallation": "",
                "SiteFinalOnly": " ",
            },
        )

        self.assertEqual(values, {})

    def test_unknown_option_input_fails_closed(self):
        with self.assertRaisesRegex(KeyError, "Unknown option input"):
            MODULE.evaluate_option(
                row_archetype_document(),
                {
                    "NumberofNewDriveways": 1,
                    "SidewalkInstallation": False,
                    "SiteFinalOnly": False,
                    "TypoField": 10,
                },
            )


    def test_inactive_scenario_fee_is_excluded_without_missing_input_review(self):
        document = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "SCENARIO-INACTIVE",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [
                {
                    "feeTemplateFeeId": "ACTIVE",
                    "name": "Active Scenario Fee",
                    "section": "configured",
                    "applicability": {
                        "status": "configured",
                        "conditions": [],
                    },
                    "calculation": {
                        "mode": "input",
                        "rule": {
                            "amount": 2,
                            "minimum": 0,
                            "maximum": 0,
                            "roundingType": 1,
                            "roundingValue": 0,
                            "inputs": [
                                {
                                    "inputKey": "ActiveQuantity",
                                    "label": "Active Quantity",
                                    "source": "custom-field",
                                }
                            ],
                        },
                    },
                },
                {
                    "feeTemplateFeeId": "INACTIVE",
                    "name": "Inactive Scenario Fee",
                    "section": "configured",
                    "applicability": {
                        "status": "configured",
                        "conditions": [],
                    },
                    "calculation": {
                        "mode": "input",
                        "rule": {
                            "amount": 3,
                            "minimum": 0,
                            "maximum": 0,
                            "roundingType": 1,
                            "roundingValue": 0,
                            "inputs": [
                                {
                                    "inputKey": "InactiveQuantity",
                                    "label": "Inactive Quantity",
                                    "source": "custom-field",
                                }
                            ],
                        },
                    },
                },
            ],
            "requiredInputs": [],
            "disclosures": [],
        }

        runtime = MODULE.evaluate_option(
            document,
            {"ActiveQuantity": 5},
            inactive_fee_ids={"INACTIVE"},
        )
        report = runtime["report"]

        self.assertEqual(report["estimatedTotal"], 10.0)
        self.assertEqual(report["blockedFees"], [])
        self.assertEqual(report["unresolvedApplicabilityFees"], [])
        self.assertEqual(runtime["inactiveFeeIds"], ["INACTIVE"])
        self.assertEqual(
            {item["feeTemplateFeeId"] for item in report["excludedFees"]},
            {"INACTIVE"},
        )


if __name__ == "__main__":
    unittest.main()
