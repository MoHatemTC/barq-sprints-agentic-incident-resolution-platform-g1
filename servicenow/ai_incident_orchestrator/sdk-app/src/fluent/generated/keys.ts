import '@servicenow/sdk/global'

declare global {
    namespace Now {
        namespace Internal {
            interface Keys extends KeysRegistry {
                explicit: {
                    bom_json: {
                        table: 'sys_module'
                        id: 'f63a6ab3e92d4288af78261e9cf42d50'
                    }
                    package_json: {
                        table: 'sys_module'
                        id: '05f9724cc56043af8f1cd2b6a4d6e0e2'
                    }
                    validate_ai_confidence_on_change: {
                        table: 'sys_script_client'
                        id: '5fa1aba4346d48548dd6e39fd569fc9e'
                    }
                    validate_ai_confidence_on_submit: {
                        table: 'sys_script_client'
                        id: '146e116470fc47089de98b045715b135'
                    }
                }
                composite: [
                    {
                        table: 'sys_documentation'
                        id: '045dbef99a5347eda5eced82009a333d'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_agent_version'
                            language: 'en'
                        }
                    },
                    {
                        table: 'sys_ui_section'
                        id: '13ecf04d29674d7a838b8524c8abb773'
                        key: {
                            name: 'incident'
                            caption: 'AI Incident Orchestrator'
                            view: 'Default view'
                            sys_domain: 'NULL'
                        }
                    },
                    {
                        table: 'sys_ui_element'
                        id: '160da7cb03784ee0a2d89db3c356339d'
                        key: {
                            sys_ui_section: {
                                id: '13ecf04d29674d7a838b8524c8abb773'
                                key: {
                                    name: 'incident'
                                    caption: 'AI Incident Orchestrator'
                                    view: 'Default view'
                                    sys_domain: 'NULL'
                                }
                            }
                            element: 'x_2215032_ai_inc_0_ai_agent_version'
                            position: '5'
                        }
                    },
                    {
                        table: 'sys_choice'
                        id: '1973e47bee8d4fdfbfaff3498ea0d742'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_processing_state'
                            value: 'complete'
                        }
                    },
                    {
                        table: 'sys_documentation'
                        id: '1ae033e28d744529896538191876f772'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_processing_start'
                            language: 'en'
                        }
                    },
                    {
                        table: 'sys_ui_element'
                        id: '1b8238b4d2d64a02b57c9e878bc795e6'
                        key: {
                            sys_ui_section: {
                                id: '13ecf04d29674d7a838b8524c8abb773'
                                key: {
                                    name: 'incident'
                                    caption: 'AI Incident Orchestrator'
                                    view: 'Default view'
                                    sys_domain: 'NULL'
                                }
                            }
                            element: 'x_2215032_ai_inc_0_ai_suggestion'
                            position: '13'
                        }
                    },
                    {
                        table: 'sys_dictionary'
                        id: '2509d229cd804e4993303adbf69bddeb'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_processing_state'
                        }
                    },
                    {
                        table: 'sys_dictionary'
                        id: '255174164b974a3bb631058f9731697e'
                        deleted: true
                        key: {
                            name: 'incident'
                            element: 'NULL'
                        }
                    },
                    {
                        table: 'sys_dictionary'
                        id: '28d1a4669a6e4ac5aecdd4e6ce371982'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_suggestion'
                        }
                    },
                    {
                        table: 'sys_dictionary'
                        id: '322ed0cfc2d44cdea795e72cb7428e13'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_model_name'
                        }
                    },
                    {
                        table: 'ua_table_licensing_config'
                        id: '33e28cd5139845b396cb900cb489542f'
                        key: {
                            name: 'incident'
                        }
                    },
                    {
                        table: 'sys_ui_element'
                        id: '346121a2614443fbb5f7995b9066b3df'
                        key: {
                            sys_ui_section: {
                                id: '13ecf04d29674d7a838b8524c8abb773'
                                key: {
                                    name: 'incident'
                                    caption: 'AI Incident Orchestrator'
                                    view: 'Default view'
                                    sys_domain: 'NULL'
                                }
                            }
                            element: '.split'
                            position: '8'
                        }
                    },
                    {
                        table: 'sys_documentation'
                        id: '364c35f3baea440488c0ff105e2762ee'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_processing_end'
                            language: 'en'
                        }
                    },
                    {
                        table: 'sys_db_object'
                        id: '3893e2b8ef16453e8d26ea44c979a544'
                        key: {
                            name: 'incident'
                        }
                    },
                    {
                        table: 'sys_ui_element'
                        id: '3c285e1e0dbb4092bb31bfd30a6343aa'
                        key: {
                            sys_ui_section: {
                                id: '13ecf04d29674d7a838b8524c8abb773'
                                key: {
                                    name: 'incident'
                                    caption: 'AI Incident Orchestrator'
                                    view: 'Default view'
                                    sys_domain: 'NULL'
                                }
                            }
                            element: 'x_2215032_ai_inc_0_ai_processing_state'
                            position: '1'
                        }
                    },
                    {
                        table: 'sys_documentation'
                        id: '3e271be88fcb4c1d912c055bebc8e0d3'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_confidence'
                            language: 'en'
                        }
                    },
                    {
                        table: 'sys_dictionary'
                        id: '44762e01b8cc48c18746cdab2fe64308'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_classification'
                        }
                    },
                    {
                        table: 'sys_ui_element'
                        id: '4929fa4e41f647be98526fde976a3769'
                        key: {
                            sys_ui_section: {
                                id: '13ecf04d29674d7a838b8524c8abb773'
                                key: {
                                    name: 'incident'
                                    caption: 'AI Incident Orchestrator'
                                    view: 'Default view'
                                    sys_domain: 'NULL'
                                }
                            }
                            element: '.end_split'
                            position: '12'
                        }
                    },
                    {
                        table: 'sys_documentation'
                        id: '4d656fefbb3c41adbd21b9c991984b12'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_enabled'
                            language: 'en'
                        }
                    },
                    {
                        table: 'sys_documentation'
                        id: '4ef6870552864f6a99ee6b8ed48e2d34'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_resolution'
                            language: 'en'
                        }
                    },
                    {
                        table: 'sys_documentation'
                        id: '61c4fcd80e3745de9ac59a9fab040ed2'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_model_name'
                            language: 'en'
                        }
                    },
                    {
                        table: 'sys_documentation'
                        id: '67bae74dc6a948bb913443e2797278d8'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_human_review_required'
                            language: 'en'
                        }
                    },
                    {
                        table: 'sys_dictionary'
                        id: '6b6aa28d9bad4d4bba7fe1ff12132446'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_human_lock'
                        }
                    },
                    {
                        table: 'sys_dictionary'
                        id: '6cb7c301091a44d68f4c7d2cb6f6195b'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_enabled'
                        }
                    },
                    {
                        table: 'sys_documentation'
                        id: '6e964895ace84283bdb9b676371d42d8'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_suggestion'
                            language: 'en'
                        }
                    },
                    {
                        table: 'sys_documentation'
                        id: '71789134c8e3403cbbeec3db1f9be3e8'
                        key: {
                            name: 'incident'
                            element: 'NULL'
                            language: 'en'
                        }
                    },
                    {
                        table: 'sys_user_role'
                        id: '7ea6fbbf738bc7502aedfed25ab8b79a'
                        key: {
                            name: 'x_2215032_ai_inc_0.admin'
                        }
                    },
                    {
                        table: 'sys_documentation'
                        id: '7f2d6fd1e9724884a2094c3071d35b57'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_human_lock'
                            language: 'en'
                        }
                    },
                    {
                        table: 'sys_documentation'
                        id: '8493d94fbc59416ca3f2c8a066257d34'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_processing_state'
                            language: 'en'
                        }
                    },
                    {
                        table: 'sys_documentation'
                        id: '8abb022b6f6e4f018b0de2dd316ccf55'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_classification'
                            language: 'en'
                        }
                    },
                    {
                        table: 'sys_ui_element'
                        id: '914a865f21de401190224e708e5c137a'
                        key: {
                            sys_ui_section: {
                                id: '13ecf04d29674d7a838b8524c8abb773'
                                key: {
                                    name: 'incident'
                                    caption: 'AI Incident Orchestrator'
                                    view: 'Default view'
                                    sys_domain: 'NULL'
                                }
                            }
                            element: 'x_2215032_ai_inc_0_ai_classification'
                            position: '2'
                        }
                    },
                    {
                        table: 'sys_choice'
                        id: '93895f6139234899b026fc1a84a6dc37'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_processing_state'
                            value: 'in_progress'
                        }
                    },
                    {
                        table: 'sys_ui_element'
                        id: '947bfb4a997c465aaf3b9481b61d3b2b'
                        key: {
                            sys_ui_section: {
                                id: '13ecf04d29674d7a838b8524c8abb773'
                                key: {
                                    name: 'incident'
                                    caption: 'AI Incident Orchestrator'
                                    view: 'Default view'
                                    sys_domain: 'NULL'
                                }
                            }
                            element: 'x_2215032_ai_inc_0_ai_failure_reason'
                            position: '11'
                        }
                    },
                    {
                        table: 'sys_ui_element'
                        id: '9574f94e67a24724b795b8170bf98fb3'
                        key: {
                            sys_ui_section: {
                                id: '13ecf04d29674d7a838b8524c8abb773'
                                key: {
                                    name: 'incident'
                                    caption: 'AI Incident Orchestrator'
                                    view: 'Default view'
                                    sys_domain: 'NULL'
                                }
                            }
                            element: 'x_2215032_ai_inc_0_ai_model_name'
                            position: '4'
                        }
                    },
                    {
                        table: 'sys_ui_form_section'
                        id: '99fc003bcb8a419a9a3dbe76b14fe67e'
                        key: {
                            sys_ui_form: '991f87290a0006414b6521d3fa9b4176'
                            sys_ui_section: {
                                id: '13ecf04d29674d7a838b8524c8abb773'
                                key: {
                                    name: 'incident'
                                    caption: 'AI Incident Orchestrator'
                                    view: 'Default view'
                                    sys_domain: 'NULL'
                                }
                            }
                        }
                    },
                    {
                        table: 'sys_ui_element'
                        id: '9be8e66364b14692a04c660a11652e1c'
                        key: {
                            sys_ui_section: {
                                id: '13ecf04d29674d7a838b8524c8abb773'
                                key: {
                                    name: 'incident'
                                    caption: 'AI Incident Orchestrator'
                                    view: 'Default view'
                                    sys_domain: 'NULL'
                                }
                            }
                            element: 'x_2215032_ai_inc_0_ai_human_review_required'
                            position: '9'
                        }
                    },
                    {
                        table: 'sys_ui_element'
                        id: '9ff65142598840ef8edbb2d64b17ef06'
                        key: {
                            sys_ui_section: {
                                id: '13ecf04d29674d7a838b8524c8abb773'
                                key: {
                                    name: 'incident'
                                    caption: 'AI Incident Orchestrator'
                                    view: 'Default view'
                                    sys_domain: 'NULL'
                                }
                            }
                            element: 'x_2215032_ai_inc_0_ai_enabled'
                            position: '0'
                        }
                    },
                    {
                        table: 'sys_dictionary'
                        id: 'a24e2b0925c84394a0ce9966d16b5e12'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_processing_start'
                        }
                    },
                    {
                        table: 'sys_choice'
                        id: 'a637df95d34147dca88eef5a5736dafe'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_processing_state'
                            value: 'awaiting_approval'
                        }
                    },
                    {
                        table: 'sys_ui_element'
                        id: 'a7a51281ecbd48d6b19036c5d1c9f3b7'
                        key: {
                            sys_ui_section: {
                                id: '13ecf04d29674d7a838b8524c8abb773'
                                key: {
                                    name: 'incident'
                                    caption: 'AI Incident Orchestrator'
                                    view: 'Default view'
                                    sys_domain: 'NULL'
                                }
                            }
                            element: 'x_2215032_ai_inc_0_ai_confidence'
                            position: '3'
                        }
                    },
                    {
                        table: 'sys_user_role'
                        id: 'b0b63fbf738bc7502aedfed25ab8b72f'
                        key: {
                            name: 'x_2215032_ai_inc_0.user'
                        }
                    },
                    {
                        table: 'sys_choice'
                        id: 'b672e58359ac4ac6a14c62140e54c5b8'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_processing_state'
                            value: 'pending'
                        }
                    },
                    {
                        table: 'sys_choice_set'
                        id: 'bd0e2d5f83904e2c9afc45fedc1d60ea'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_processing_state'
                        }
                    },
                    {
                        table: 'sys_ui_element'
                        id: 'bf7bec8ed0a84f5c8b9773330850eea2'
                        key: {
                            sys_ui_section: {
                                id: '13ecf04d29674d7a838b8524c8abb773'
                                key: {
                                    name: 'incident'
                                    caption: 'AI Incident Orchestrator'
                                    view: 'Default view'
                                    sys_domain: 'NULL'
                                }
                            }
                            element: 'x_2215032_ai_inc_0_ai_human_lock'
                            position: '10'
                        }
                    },
                    {
                        table: 'sys_documentation'
                        id: 'c42cec10571e47668668b9d69f3d97d5'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_failure_reason'
                            language: 'en'
                        }
                    },
                    {
                        table: 'sys_dictionary'
                        id: 'c7ff11eb5d1e4e1ca6383018455bee1e'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_human_review_required'
                        }
                    },
                    {
                        table: 'sys_ui_element'
                        id: 'cb5b3f2920dc47bba396b1bcd966a32d'
                        key: {
                            sys_ui_section: {
                                id: '13ecf04d29674d7a838b8524c8abb773'
                                key: {
                                    name: 'incident'
                                    caption: 'AI Incident Orchestrator'
                                    view: 'Default view'
                                    sys_domain: 'NULL'
                                }
                            }
                            element: 'x_2215032_ai_inc_0_ai_processing_start'
                            position: '6'
                        }
                    },
                    {
                        table: 'sys_dictionary'
                        id: 'cce4b3a5c3f7445d97ea0d3d9aed85d6'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_failure_reason'
                        }
                    },
                    {
                        table: 'sys_ui_element'
                        id: 'd525ade0c40d42a48b7ee9a4f30db887'
                        key: {
                            sys_ui_section: {
                                id: '13ecf04d29674d7a838b8524c8abb773'
                                key: {
                                    name: 'incident'
                                    caption: 'AI Incident Orchestrator'
                                    view: 'Default view'
                                    sys_domain: 'NULL'
                                }
                            }
                            element: 'x_2215032_ai_inc_0_ai_processing_end'
                            position: '7'
                        }
                    },
                    {
                        table: 'sys_dictionary'
                        id: 'd772b9a2b8cc47ad9b94c1e6b1b02de8'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_confidence'
                        }
                    },
                    {
                        table: 'sys_dictionary'
                        id: 'e38c379b182e4a1187f97f26101e2c1b'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_agent_version'
                        }
                    },
                    {
                        table: 'sys_ui_element'
                        id: 'e5ccb154ecd84866a6459e6109b4629e'
                        key: {
                            sys_ui_section: {
                                id: '13ecf04d29674d7a838b8524c8abb773'
                                key: {
                                    name: 'incident'
                                    caption: 'AI Incident Orchestrator'
                                    view: 'Default view'
                                    sys_domain: 'NULL'
                                }
                            }
                            element: 'x_2215032_ai_inc_0_ai_resolution'
                            position: '14'
                        }
                    },
                    {
                        table: 'sys_dictionary'
                        id: 'e7b7854cecd84d9d8fcbd868095e1b54'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_resolution'
                        }
                    },
                    {
                        table: 'sys_dictionary'
                        id: 'e7ba12d061e846208eeaf4362f2fcd34'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_processing_end'
                        }
                    },
                    {
                        table: 'sys_choice'
                        id: 'f27cc07d444e491f8aa7a71cd3421155'
                        key: {
                            name: 'incident'
                            element: 'x_2215032_ai_inc_0_ai_processing_state'
                            value: 'failed'
                        }
                    },
                ]
            }
        }
    }
}
