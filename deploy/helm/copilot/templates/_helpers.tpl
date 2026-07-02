{{- define "copilot.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "copilot.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "copilot.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "copilot.labels" -}}
app.kubernetes.io/name: {{ include "copilot.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "copilot.selectorLabels" -}}
app.kubernetes.io/name: {{ include "copilot.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "copilot.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "copilot.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "copilot.secretName" -}}
{{- if .Values.secret.existingSecret -}}{{ .Values.secret.existingSecret }}{{- else -}}{{ include "copilot.fullname" . }}{{- end -}}
{{- end -}}
