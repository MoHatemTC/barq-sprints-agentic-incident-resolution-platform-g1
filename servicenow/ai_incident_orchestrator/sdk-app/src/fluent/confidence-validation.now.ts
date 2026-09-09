import { ClientScript } from '@servicenow/sdk/core'

/**
 * Immediate form enforcement for the S1.1 0.00–1.00 confidence contract.
 * Server/API writers must apply the same contract before writing; a scripted
 * before Business Rule in this scope cannot abort writes to Global Incident.
 */
export const validateAiConfidenceOnChange = ClientScript({
    $id: Now.ID['validate_ai_confidence_on_change'],
    name: 'AI IO - Validate Confidence (Change)',
    table: 'incident',
    type: 'onChange',
    field: 'x_2215032_ai_inc_0_ai_confidence',
    active: true,
    appliesExtended: false,
    global: true,
    uiType: 'all',
    isolateScript: true,
    description: 'Rejects form values outside the inclusive 0.00 to 1.00 confidence range.',
    script: `function onChange(control, oldValue, newValue, isLoading) {
        if (isLoading || newValue === '') return;
        var value = Number(newValue);
        if (!isFinite(value) || value < 0 || value > 1) {
            g_form.setValue('x_2215032_ai_inc_0_ai_confidence', oldValue || '');
            g_form.showErrorBox('x_2215032_ai_inc_0_ai_confidence', 'AI Confidence must be between 0.00 and 1.00.', true);
        } else {
            g_form.hideFieldMsg('x_2215032_ai_inc_0_ai_confidence', true);
        }
    }`,
})

export const validateAiConfidenceOnSubmit = ClientScript({
    $id: Now.ID['validate_ai_confidence_on_submit'],
    name: 'AI Incident Orchestrator - Validate Confidence on Submit',
    table: 'incident',
    type: 'onSubmit',
    active: true,
    appliesExtended: false,
    global: true,
    uiType: 'all',
    isolateScript: true,
    description: 'Prevents form submission when AI Confidence is outside 0.00 to 1.00.',
    script: `function onSubmit() {
        var rawValue = g_form.getValue('x_2215032_ai_inc_0_ai_confidence');
        if (rawValue === '') return true;
        var value = Number(rawValue);
        if (!isFinite(value) || value < 0 || value > 1) {
            g_form.showErrorBox('x_2215032_ai_inc_0_ai_confidence', 'AI Confidence must be between 0.00 and 1.00.', true);
            return false;
        }
        return true;
    }`,
})
