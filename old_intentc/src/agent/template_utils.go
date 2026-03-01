package agent

import (
	"bytes"
	"fmt"
	"strings"
	"text/template"

	"github.com/pboueri/intentc/src"
)

// PrepareTemplateData creates common template data from a build context
func PrepareTemplateData(buildCtx BuildContext) map[string]interface{} {
	// Prepare dependencies string
	dependencies := ""
	if len(buildCtx.Intent.Dependencies) > 0 {
		dependencies = strings.Join(buildCtx.Intent.Dependencies, ", ")
	}

	// Base template data
	data := map[string]interface{}{
		"ProjectRoot":   buildCtx.ProjectRoot,
		"GenerationID":  buildCtx.GenerationID,
		"IntentName":    buildCtx.Intent.Name,
		"IntentContent": buildCtx.Intent.Content,
		"Dependencies":  dependencies,
		"BuildName":     buildCtx.BuildName,
		"BuildPath":     buildCtx.BuildPath,
	}

	// Convert validations to template-friendly format
	if len(buildCtx.Validations) > 0 {
		var validations []map[string]interface{}
		for _, valFile := range buildCtx.Validations {
			var vals []map[string]interface{}
			for _, val := range valFile.Validations {
				valData := map[string]interface{}{
					"Name":        val.Name,
					"Type":        string(val.Type),
					"Description": val.Description,
				}
				if details := GetValidationDetails(&val); details != "" {
					valData["Details"] = details
				}
				vals = append(vals, valData)
			}
			validations = append(validations, map[string]interface{}{
				"Validations": vals,
			})
		}
		data["Validations"] = validations
	}

	return data
}

// ExecuteTemplate executes a template with the given data
func ExecuteTemplate(templateStr string, data interface{}) (string, error) {
	tmpl, err := template.New("prompt").Parse(templateStr)
	if err != nil {
		return "", fmt.Errorf("failed to parse template: %w", err)
	}

	var buf bytes.Buffer
	if err := tmpl.Execute(&buf, data); err != nil {
		return "", fmt.Errorf("failed to execute template: %w", err)
	}

	return buf.String(), nil
}

// PrepareRefinementData creates template data for refinement
func PrepareRefinementData(target *src.Target, userPrompt string, generatedFiles []string, validationErrors []string) map[string]interface{} {
	return map[string]interface{}{
		"TargetName":       target.Name,
		"UserFeedback":     userPrompt,
		"GeneratedFiles":   generatedFiles,
		"ValidationErrors": validationErrors,
	}
}

// PrepareValidationData creates template data for validation
func PrepareValidationData(validation *src.Validation, generatedFiles []string) map[string]interface{} {
	return map[string]interface{}{
		"ValidationName":        validation.Name,
		"ValidationType":        string(validation.Type),
		"ValidationDescription": validation.Description,
		"ValidationDetails":     GetValidationDetails(validation),
		"GeneratedFiles":        generatedFiles,
	}
}