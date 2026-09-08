/*
 * Reference logic for the scoped Incident Business Rule:
 * "Validate AI confidence range"
 *
 * Configure in ServiceNow as:
 *   Table: Incident [incident]
 *   When: before
 *   Insert: true
 *   Update: true
 *   Advanced: true
 *   Application: AI Incident Orchestrator (never Global)
 *
 * Replace the placeholder with the exact scope-prefixed column name created
 * by ServiceNow. The exported update-set XML, not this reference file, is the
 * deployable artifact.
 */
(function executeRule(current, previous) {
    'use strict';

    var confidenceField = 'REPLACE_WITH_SCOPED_CONFIDENCE_COLUMN';
    var confidenceElement = current.getElement(confidenceField);

    if (!confidenceElement || confidenceElement.nil() || !confidenceElement.changes()) {
        return;
    }

    var rawValue = current.getValue(confidenceField);
    var confidence = Number(rawValue);

    if (!isFinite(confidence) || confidence < 0 || confidence > 1) {
        gs.addErrorMessage('AI Confidence must be a decimal between 0 and 1 inclusive.');
        current.setAbortAction(true);
    }
})(current, previous);

