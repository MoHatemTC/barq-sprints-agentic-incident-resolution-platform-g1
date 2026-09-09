import { Record } from '@servicenow/sdk/core'

/** Dedicated section on the standard Incident Default view. */
export const aiIncidentOrchestratorSection = Record({
    $id: Now.ID['ai_incident_orchestrator_section'],
    table: 'sys_ui_section',
    data: {
        name: 'incident',
        caption: 'AI Incident Orchestrator',
        view: 'Default view',
        title: false,
        header: false,
    },
})

/** Append the scoped section after the four baseline Default-view sections. */
export const aiIncidentOrchestratorFormSection = Record({
    $id: Now.ID['ai_incident_orchestrator_form_section'],
    table: 'sys_ui_form_section',
    data: {
        sys_ui_form: '991f87290a0006414b6521d3fa9b4176',
        sys_ui_section: aiIncidentOrchestratorSection,
        position: 4,
    },
})

export const aiEnabledElement = Record({ $id: Now.ID['form_element_00'], table: 'sys_ui_element', data: { sys_ui_section: aiIncidentOrchestratorSection, element: 'x_2215032_ai_inc_0_ai_enabled', type: 'element', position: 0 } })
export const aiStateElement = Record({ $id: Now.ID['form_element_01'], table: 'sys_ui_element', data: { sys_ui_section: aiIncidentOrchestratorSection, element: 'x_2215032_ai_inc_0_ai_processing_state', type: 'element', position: 1 } })
export const aiClassificationElement = Record({ $id: Now.ID['form_element_02'], table: 'sys_ui_element', data: { sys_ui_section: aiIncidentOrchestratorSection, element: 'x_2215032_ai_inc_0_ai_classification', type: 'element', position: 2 } })
export const aiConfidenceElement = Record({ $id: Now.ID['form_element_03'], table: 'sys_ui_element', data: { sys_ui_section: aiIncidentOrchestratorSection, element: 'x_2215032_ai_inc_0_ai_confidence', type: 'element', position: 3 } })
export const aiModelElement = Record({ $id: Now.ID['form_element_04'], table: 'sys_ui_element', data: { sys_ui_section: aiIncidentOrchestratorSection, element: 'x_2215032_ai_inc_0_ai_model_name', type: 'element', position: 4 } })
export const aiVersionElement = Record({ $id: Now.ID['form_element_05'], table: 'sys_ui_element', data: { sys_ui_section: aiIncidentOrchestratorSection, element: 'x_2215032_ai_inc_0_ai_agent_version', type: 'element', position: 5 } })
export const aiStartElement = Record({ $id: Now.ID['form_element_06'], table: 'sys_ui_element', data: { sys_ui_section: aiIncidentOrchestratorSection, element: 'x_2215032_ai_inc_0_ai_processing_start', type: 'element', position: 6 } })
export const aiEndElement = Record({ $id: Now.ID['form_element_07'], table: 'sys_ui_element', data: { sys_ui_section: aiIncidentOrchestratorSection, element: 'x_2215032_ai_inc_0_ai_processing_end', type: 'element', position: 7 } })
export const splitElement = Record({ $id: Now.ID['form_element_08'], table: 'sys_ui_element', data: { sys_ui_section: aiIncidentOrchestratorSection, element: '.split', type: '.split', position: 8 } })
export const aiReviewElement = Record({ $id: Now.ID['form_element_09'], table: 'sys_ui_element', data: { sys_ui_section: aiIncidentOrchestratorSection, element: 'x_2215032_ai_inc_0_ai_human_review_required', type: 'element', position: 9 } })
export const aiLockElement = Record({ $id: Now.ID['form_element_10'], table: 'sys_ui_element', data: { sys_ui_section: aiIncidentOrchestratorSection, element: 'x_2215032_ai_inc_0_ai_human_lock', type: 'element', position: 10 } })
export const aiFailureElement = Record({ $id: Now.ID['form_element_11'], table: 'sys_ui_element', data: { sys_ui_section: aiIncidentOrchestratorSection, element: 'x_2215032_ai_inc_0_ai_failure_reason', type: 'element', position: 11 } })
export const endSplitElement = Record({ $id: Now.ID['form_element_12'], table: 'sys_ui_element', data: { sys_ui_section: aiIncidentOrchestratorSection, element: '.end_split', type: '.end_split', position: 12 } })
export const aiSuggestionElement = Record({ $id: Now.ID['form_element_13'], table: 'sys_ui_element', data: { sys_ui_section: aiIncidentOrchestratorSection, element: 'x_2215032_ai_inc_0_ai_suggestion', type: 'element', position: 13 } })
export const aiResolutionElement = Record({ $id: Now.ID['form_element_14'], table: 'sys_ui_element', data: { sys_ui_section: aiIncidentOrchestratorSection, element: 'x_2215032_ai_inc_0_ai_resolution', type: 'element', position: 14 } })
