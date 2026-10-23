import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPTS_DIR / "option_ui_schema.py"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

SPEC = importlib.util.spec_from_file_location("option_ui_schema", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def document_with_fields():
    return {
        "option": {
            "optionKey": "TEST",
            "permitType": "Test Permit",
            "workClass": "Test Work Class",
        },
        "fees": [
            {
                "feeTemplateFeeId": "QTY",
                "name": "Quantity Fee",
                "section": "input-required",
                "applicability": {
                    "status": "configured",
                    "conditions": [],
                },
                "calculation": {
                    "mode": "input",
                    "rule": {
                        "amount": 10,
                        "inputs": [
                            {
                                "inputKey": "Quantity",
                                "label": "Quantity",
                                "source": "custom-field",
                            }
                        ],
                    },
                },
            },
            {
                "feeTemplateFeeId": "BOOL",
                "name": "Boolean Fee",
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
                                        "operandValue": "NeedsFee",
                                        "operandFriendlyName": "Needs Fee",
                                        "operatorID": 1,
                                        "compareExpression": {"value": True},
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
        ],
        "requiredInputs": [],
    }


class OptionUiSchemaTests(unittest.TestCase):
    def test_numeric_and_boolean_controls_are_display_ready(self):
        schema = MODULE.build_option_ui_schema(document_with_fields())
        controls = {
            item["key"]: item
            for item in schema["controls"]
        }

        self.assertTrue(schema["displayReady"])
        self.assertEqual(schema["uiBlockers"], [])
        self.assertEqual(controls["Quantity"]["controlType"], "number")
        self.assertEqual(controls["NeedsFee"]["controlType"], "boolean")
        self.assertEqual(
            controls["NeedsFee"]["choices"],
            [
                {"value": False, "label": "No"},
                {"value": True, "label": "Yes"},
            ],
        )

    def test_direct_prevalence_attaches_by_input_key(self):
        document = document_with_fields()
        document["inputPrevalence"] = [
            {
                "inputKey": "Quantity",
                "status": "available",
                "denominatorName": "MATURE_ISSUED_OR_FINALIZED",
                "denominator": 100,
                "activeRows": 42,
                "activeRate": 0.42,
                "activeDefinition": "numeric <> 0",
                "basis": "direct-input-prevalence-query",
            }
        ]

        schema = MODULE.build_option_ui_schema(document)
        controls = {
            item["key"]: item
            for item in schema["controls"]
        }

        self.assertEqual(
            controls["Quantity"]["historicalPrevalence"]["activeRate"],
            0.42,
        )
        self.assertEqual(
            controls["Quantity"]["historicalPrevalence"]["basis"],
            "direct-input-prevalence-query",
        )
        self.assertEqual(
            controls["NeedsFee"]["historicalPrevalence"]["status"],
            "unavailable",
        )

    def test_technical_field_suffixes_are_removed_from_display_label(self):
        document = document_with_fields()
        document["fees"][0]["calculation"]["rule"]["inputs"][0]["label"] = (
            "Quantity (Quantity, Number)"
        )
        document["fees"][1]["applicability"]["conditions"][0]["sourceRule"][
            "childConditions"
        ][0]["operandFriendlyName"] = (
            "Needs Fee (PM - [TEST] Internal Template)"
        )

        schema = MODULE.build_option_ui_schema(document)
        controls = {
            item["key"]: item
            for item in schema["controls"]
        }

        self.assertEqual(
            controls["Quantity"]["displayLabel"],
            "Quantity",
        )
        self.assertEqual(
            controls["NeedsFee"]["displayLabel"],
            "Needs Fee",
        )

    def test_infrastructure_style_labels_are_humanized_and_disambiguated(self):
        document = {
            "option": {
                "optionKey": "INFRA",
                "permitType": "Generic Infrastructure",
                "workClass": "Generic Work",
            },
            "fees": [],
            "requiredInputs": [],
        }
        fields = [
            (
                "PrivateSewerLFTotal",
                "PRIVATE IMPROVEMENT QUANTITIES "
                "(PrivateSewerLFTotal, Text)",
            ),
            (
                "PrivateWaterLFTotal",
                "PRIVATE IMPROVEMENT QUANTITIES "
                "(PrivateWaterLFTotal, Text)",
            ),
            (
                "SewerLF",
                "SewerLF(PUBLIC IMPROVEMENT QUANTITIES-PM - "
                "[Infrastructure] Sewer)",
            ),
            (
                "TotalStreetFullLF",
                "TotalStreetFullLF(PUBLIC IMPROVEMENT QUANTITIES-PM - "
                "[Infrastructure] Street)",
            ),
            (
                "TotalSewerLF",
                "TotalSewerLF(PUBLIC IMPROVEMENT QUANTITIES-PM - "
                "[Infrastructure] Street)",
            ),
        ]
        for index, (key, label) in enumerate(fields):
            document["fees"].append(
                {
                    "feeTemplateFeeId": f"FEE-{index}",
                    "name": f"Fee {index}",
                    "section": "input-required",
                    "applicability": {
                        "status": "configured",
                        "conditions": [],
                    },
                    "calculation": {
                        "mode": "input",
                        "rule": {
                            "amount": 1,
                            "inputs": [
                                {
                                    "inputKey": key,
                                    "label": label,
                                    "source": "custom-field",
                                }
                            ],
                        },
                    },
                }
            )

        schema = MODULE.build_option_ui_schema(document)
        controls = {
            control["key"]: control
            for control in schema["controls"]
        }

        self.assertEqual(
            controls["PrivateSewerLFTotal"]["displayLabel"],
            "Private Sewer Linear Feet",
        )
        self.assertEqual(
            controls["PrivateWaterLFTotal"]["displayLabel"],
            "Private Water Linear Feet",
        )
        self.assertEqual(
            controls["SewerLF"]["displayLabel"],
            "Sewer Linear Feet",
        )
        self.assertEqual(
            controls["TotalStreetFullLF"]["displayLabel"],
            "Total Street Full Linear Feet",
        )
        self.assertEqual(
            controls["TotalSewerLF"]["displayLabel"],
            "Total Sewer Linear Feet",
        )
        self.assertEqual(
            len({
                control["displayLabel"]
                for control in controls.values()
            }),
            5,
        )

    def test_curated_aliases_clean_known_internal_labels_only(self):
        document = {
            "option": {
                "optionKey": "ALIASES",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [],
            "requiredInputs": [],
        }
        fields = [
            ("Workingwithouta", "Workingwithouta"),
            ("Both_Mech_and_Ele", "Both_Mech_and_Ele"),
            ("ROW", "ROW"),
            ("ADA", "ADA"),
        ]
        for index, (key, label) in enumerate(fields):
            document["fees"].append(
                {
                    "feeTemplateFeeId": f"ALIAS-{index}",
                    "name": f"Alias Fee {index}",
                    "section": "input-required",
                    "applicability": {
                        "status": "configured",
                        "conditions": [],
                    },
                    "calculation": {
                        "mode": "input",
                        "rule": {
                            "amount": 1,
                            "inputs": [
                                {
                                    "inputKey": key,
                                    "label": label,
                                    "source": "custom-field",
                                }
                            ],
                        },
                    },
                }
            )

        schema = MODULE.build_option_ui_schema(document)
        controls = {
            control["key"]: control
            for control in schema["controls"]
        }

        self.assertEqual(
            controls["Workingwithouta"]["displayLabel"],
            "Working without a",
        )
        self.assertEqual(
            controls["Both_Mech_and_Ele"]["displayLabel"],
            "Both Mechanical and Electrical",
        )
        self.assertEqual(controls["ROW"]["displayLabel"], "ROW")
        self.assertEqual(controls["ADA"]["displayLabel"], "ADA")
        self.assertEqual(
            MODULE._scenario_group_label(
                {
                    "label":
                        "Field(Workingwithouta - PM - [X] Template)"
                }
            ),
            "Working without a",
        )
        self.assertEqual(
            MODULE._scenario_group_label(
                {
                    "label":
                        "Field(Both_Mech_and_Ele - PM - [X] Template)"
                }
            ),
            "Both Mechanical and Electrical",
        )

    def test_option_label_mode_preserves_internal_and_defaults_external(self):
        internal = "DSENG - Right of Way"
        self.assertEqual(
            MODULE.display_option_label(internal, "internal"),
            internal,
        )
        self.assertEqual(
            MODULE.display_option_label(internal, "external"),
            "Right of Way",
        )
        self.assertEqual(
            MODULE.display_option_label(
                "DSINSP - Online Only MENR/ELNR Changeout - Combo",
                "external",
            ),
            "Online Only Mechanical / Electrical - Non-Residential Change Out - Combo",
        )
        self.assertEqual(
            MODULE.display_option_label("Change Out", "external"),
            "Change Out",
        )
        with self.assertRaises(ValueError):
            MODULE.display_option_label(internal, "unknown")

    def test_approved_staff_facing_cleanup_rules(self):
        self.assertEqual(
            MODULE.clean_staff_facing_label(
                "Workingwithouta Permit",
                surface="control",
            ),
            "Working without a Permit",
        )
        self.assertEqual(
            MODULE.clean_staff_facing_label(
                "Numberof AUTOFIREEXTINGUISHIGNSYSTEMS",
                surface="control",
            ),
            "Number of Auto Fire Extinguishing Systems",
        )
        self.assertEqual(
            MODULE.clean_staff_facing_label(
                "Installed By (PM - [PU] Merger Taps (Wendell))",
                surface="control",
                strip_technical_context=True,
            ),
            "Installed By",
        )
        self.assertEqual(
            MODULE.clean_staff_facing_label(
                "Single Family3000to3999",
                surface="control",
            ),
            "Single Family 3,000 to 3,999",
        )
        self.assertEqual(
            MODULE.clean_staff_facing_label(
                "Conditional Service Fee [ELNR] Multi Fam",
                surface="fee",
            ),
            "Conditional Service Fee [Non-Residential Electrical] Multi Family",
        )
        self.assertEqual(
            MODULE.clean_staff_facing_label(
                '8" Water Capital Facililty Fee',
                surface="fee",
            ),
            '8" Water Capital Facility Fee',
        )
        self.assertEqual(
            MODULE.clean_staff_facing_label(
                "CITY-WAKE FOREST",
                surface="choice",
            ),
            "City - Wake Forest",
        )
        self.assertEqual(
            MODULE.clean_staff_facing_label("ROW", surface="control"),
            "ROW",
        )
        self.assertEqual(
            MODULE.clean_staff_facing_label("ADA", surface="control"),
            "ADA",
        )

    def test_curated_aliases_apply_to_choice_labels_without_changing_values(self):
        field = {
            "key": "PermitScope",
            "label": "Permit Scope",
            "valueType": "choice",
            "source": "custom-field",
            "usages": ["applicability"],
            "requiredForFeeIds": ["CHOICE"],
            "choices": [
                {
                    "value": "Workingwithouta",
                    "label": "Workingwithouta",
                },
                {
                    "value": "Both_Mech_and_Ele",
                    "label": "Both_Mech_and_Ele",
                },
                {
                    "value": "ROW",
                    "label": "ROW",
                },
            ],
        }

        control = MODULE._control_for_field(field)

        self.assertEqual(
            control["choices"],
            [
                {
                    "value": "Workingwithouta",
                    "label": "Working without a",
                },
                {
                    "value": "Both_Mech_and_Ele",
                    "label": "Both Mechanical and Electrical",
                },
                {
                    "value": "ROW",
                    "label": "ROW",
                },
            ],
        )
        self.assertTrue(control["displayReady"])

    def test_duplicate_visible_choice_labels_fail_closed(self):
        document = document_with_fields()
        for index, value in enumerate(("ZONE-A", "ZONE-B"), start=1):
            document["fees"].append(
                {
                    "feeTemplateFeeId": f"ZONE-{index}",
                    "name": f"Zone Fee {index}",
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
                                            "operandValue": "Zone",
                                            "operandFriendlyName": "Zone",
                                            "operatorID": 1,
                                            "compareExpression": {
                                                "value": value,
                                                "label": "Zone 1",
                                            },
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": index * 10,
                        "rule": {},
                    },
                }
            )

        schema = MODULE.build_option_ui_schema(document)
        control = next(
            item for item in schema["controls"]
            if item["key"] == "Zone"
        )

        self.assertFalse(schema["displayReady"])
        self.assertFalse(control["displayReady"])
        self.assertEqual(
            control["duplicateChoiceLabels"],
            ["Zone 1"],
        )
        self.assertEqual(
            schema["uiBlockers"][0]["code"],
            "DUPLICATE_CHOICE_LABELS_NOT_DISPLAY_READY",
        )

    def test_repeated_same_choice_value_is_not_ambiguous(self):
        field = {
            "key": "Zone",
            "label": "Zone",
            "valueType": "choice",
            "source": "custom-field",
            "usages": ["applicability"],
            "requiredForFeeIds": ["ZONE"],
            "choices": [
                {"value": "ZONE-A", "label": "Zone 1"},
                {"value": "ZONE-A", "label": "Zone 1"},
            ],
        }

        control = MODULE._control_for_field(field)

        self.assertTrue(control["displayReady"])
        self.assertEqual(control["duplicateChoiceLabels"], [])

    def test_raw_guid_choice_blocks_staff_display(self):
        document = document_with_fields()
        document["fees"].append(
            {
                "feeTemplateFeeId": "CHOICE",
                "name": "Choice Fee",
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
                                        "operandValue": "ImpactType",
                                        "operandFriendlyName": "Impact Type",
                                        "operatorID": 1,
                                        "compareExpression": {
                                            "value":
                                                "8063845d-b589-4cab-8f78-0e0cde78647b"
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                },
                "calculation": {
                    "mode": "fixed",
                    "fixedAmount": 126,
                    "rule": {},
                },
            }
        )

        schema = MODULE.build_option_ui_schema(document)
        controls = {
            item["key"]: item
            for item in schema["controls"]
        }

        self.assertFalse(schema["displayReady"])
        self.assertEqual(
            controls["ImpactType"]["controlType"],
            "select",
        )
        self.assertFalse(controls["ImpactType"]["displayReady"])
        self.assertEqual(
            schema["uiBlockers"][0]["code"],
            "CHOICE_LABELS_NOT_DISPLAY_READY",
        )


    def test_dense_controls_get_generic_progressive_groups(self):
        document = {
            "option": {
                "optionKey": "DENSE",
                "permitType": "Generic Dense Permit",
                "workClass": "Generic Dense Work",
            },
            "fees": [],
            "requiredInputs": [],
        }

        labels = [
            "Primary(FULL STREETS - MAJOR CLOSURES-PM - [X] Dense)",
            "Secondary(FULL STREETS - MAJOR CLOSURES-PM - [X] Dense)",
            "Duration(FULL STREETS - MAJOR CLOSURES-PM - [X] Dense)",
            "Review (PM - [X] Dense)",
        ]
        for index in range(12):
            key = f"Field{index}"
            label = labels[index] if index < len(labels) else f"Field {index}"
            document["fees"].append(
                {
                    "feeTemplateFeeId": f"FEE-{index}",
                    "name": (
                        "ROW - Full Streets - Major - Review"
                        if index == 3
                        else f"Fee {index}"
                    ),
                    "section": "input-required",
                    "templateRelationship": (
                        "Direct" if index < 4 or index == 11 else "Child"
                    ),
                    "applicability": {
                        "status": "configured",
                        "conditions": [],
                    },
                    "calculation": {
                        "mode": "input",
                        "rule": {
                            "amount": 1,
                            "inputs": [
                                {
                                    "inputKey": key,
                                    "label": label,
                                    "source": "custom-field",
                                }
                            ],
                        },
                    },
                }
            )

        schema = MODULE.build_option_ui_schema(document)
        self.assertEqual(schema["presentation"]["mode"], "progressive")
        self.assertEqual(schema["presentation"]["initialControlCount"], 1)
        groups = {
            group["label"]: group
            for group in schema["presentation"]["groups"]
        }
        self.assertEqual(
            len(groups["Full Streets - Major Closures"]["controlKeys"]),
            4,
        )
        self.assertEqual(
            len(groups["Additional fee details"]["controlKeys"]),
            7,
        )
        controls = {
            control["key"]: control
            for control in schema["controls"]
        }
        self.assertIsNone(controls["Field11"]["presentationGroup"])

    def test_small_option_remains_flat(self):
        schema = MODULE.build_option_ui_schema(document_with_fields())
        self.assertEqual(schema["presentation"]["mode"], "flat")
        self.assertEqual(schema["presentation"]["groups"], [])


    def test_scenario_inactivity_preserves_shared_and_ungrouped_fees(self):
        ui_schema = {
            "controls": [
                {
                    "key": "Major",
                    "displayReady": True,
                    "controlType": "number",
                    "valueType": "number",
                    "requiredForFeeIds": ["MAJOR", "SHARED"],
                    "presentationGroup": {
                        "key": "scenario:major",
                        "kind": "scenario",
                    },
                },
                {
                    "key": "Minor",
                    "displayReady": True,
                    "controlType": "number",
                    "valueType": "number",
                    "requiredForFeeIds": ["MINOR", "SHARED"],
                    "presentationGroup": {
                        "key": "scenario:minor",
                        "kind": "scenario",
                    },
                },
                {
                    "key": "Base",
                    "displayReady": True,
                    "controlType": "number",
                    "valueType": "number",
                    "requiredForFeeIds": ["BASE"],
                    "presentationGroup": None,
                },
            ],
            "presentation": {
                "groups": [
                    {
                        "key": "scenario:major",
                        "kind": "scenario",
                        "controlKeys": ["Major"],
                    },
                    {
                        "key": "scenario:minor",
                        "kind": "scenario",
                        "controlKeys": ["Minor"],
                    },
                ]
            },
        }

        inactive = MODULE.derive_inactive_scenario_fee_ids(
            ui_schema,
            {
                "Major": "10",
                "Minor": "",
                "Base": "",
            },
        )

        self.assertEqual(inactive, ["MINOR"])

    def test_numeric_zero_does_not_activate_scenario_but_choice_no_does(self):
        ui_schema = {
            "controls": [
                {
                    "key": "Quantity",
                    "displayReady": True,
                    "controlType": "number",
                    "valueType": "number",
                    "requiredForFeeIds": ["NUMERIC"],
                    "presentationGroup": {
                        "key": "scenario:numeric",
                        "kind": "scenario",
                    },
                },
                {
                    "key": "Review",
                    "displayReady": True,
                    "controlType": "select",
                    "valueType": "choice",
                    "requiredForFeeIds": ["CHOICE"],
                    "presentationGroup": {
                        "key": "scenario:choice",
                        "kind": "scenario",
                    },
                },
            ],
            "presentation": {
                "groups": [
                    {
                        "key": "scenario:numeric",
                        "kind": "scenario",
                        "controlKeys": ["Quantity"],
                    },
                    {
                        "key": "scenario:choice",
                        "kind": "scenario",
                        "controlKeys": ["Review"],
                    },
                ]
            },
        }

        inactive = MODULE.derive_inactive_scenario_fee_ids(
            ui_schema,
            {
                "Quantity": "0",
                "Review": "No",
            },
        )

        self.assertEqual(inactive, ["NUMERIC"])


if __name__ == "__main__":
    unittest.main()
