{{- define "changedetection.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "changedetection.labels" -}}
app.kubernetes.io/name: {{ include "changedetection.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- /* Flux appends the source commit to the chart version when reconcileStrategy is Revision,
       giving "0.1.0+abc1234" — and "+" is not allowed in a label value, so every object in the
       release fails to apply. Replacing it is what the upstream Helm scaffold does. */}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end -}}
