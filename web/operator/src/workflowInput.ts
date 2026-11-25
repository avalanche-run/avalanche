import { isUnknownRecord } from "./guards";
import { referenceName, schemaTypeLabel } from "./schemaType";
import type { FlowInfoMsg, RunInputMsg, TriggerKind } from "./model";
import {
  parseJsonValue,
  parseNullableSchema,
  type JsonSchema,
  type JsonValue,
  type Schema,
} from "./stepInterface";

/** Graph node ID for the run input; the colon keeps it apart from Python node IDs. */
export const INPUT_NODE_ID = "ava:input";

export interface WorkflowInputDeclaration {
  typeName: string;
  jsonSchema: JsonSchema;
  consumerNodeIds: string[];
}

/**
 * How a field gets its value when the caller omits it. A "computed" default comes from
 * a `default_factory`, which the JSON schema cannot state, so each run computes it.
 */
export type FieldDefault =
  { kind: "required" } | { kind: "value"; value: JsonValue } | { kind: "computed" };

export interface WorkflowInputField {
  name: string;
  typeLabel: string;
  default: FieldDefault;
  description?: string;
  schema: Schema;
  /** Set when the field takes an `ava.File` ("single") or a `list[ava.File]` ("list"). */
  file?: FileFieldKind;
  /** Where a nested field sits in the input, from the top-level field; absent at the top. */
  path?: string[];
  /** Set when the field takes a model: the model's fields, edited as a group. */
  model?: { fields: WorkflowInputField[] };
}

export type FileFieldKind = "single" | "list";

/** Whether a `$ref` names the `ava.File` model, which pins `kind` to "file". */
function refersToFile(schema: Schema, definitions: Record<string, Schema>): boolean {
  if (typeof schema === "boolean" || schema.$ref === undefined) return false;
  const target = definitions[referenceName(schema.$ref)];
  if (target === undefined || typeof target === "boolean") return false;
  const kind = target.properties?.kind;
  return kind !== undefined && typeof kind !== "boolean" && kind.const === "file";
}

/** How a field takes files, looking through an optional `| None`. */
function fileFieldKind(
  schema: Schema,
  definitions: Record<string, Schema>,
): FileFieldKind | undefined {
  if (typeof schema === "boolean") return undefined;
  if (schema.anyOf) {
    const [kind, ...others] = schema.anyOf
      .filter((option) => typeof option === "boolean" || option.type !== "null")
      .map((option) => fileFieldKind(option, definitions));
    return others.length === 0 ? kind : undefined;
  }
  if (refersToFile(schema, definitions)) return "single";
  if (
    schema.type === "array" &&
    schema.items !== undefined &&
    !Array.isArray(schema.items) &&
    refersToFile(schema.items, definitions)
  ) {
    return "list";
  }
  return undefined;
}

/**
 * Where a run's field value came from. "unrecorded" fields are excluded from
 * serialization, such as `Field(exclude=True)` secrets, so the run has no value to show.
 * "computed" fields took a `default_factory` default that a submitted-input record
 * cannot know, since the run computed it when it started.
 */
export type RunValueSource = "supplied" | "default" | "computed" | "unrecorded";

/** One field's value in a run, and where that value came from. */
export interface RunInputField extends WorkflowInputField {
  /** Absent when the field is unrecorded. */
  value?: JsonValue;
  source: RunValueSource;
}

function stringList(value: unknown, path: string): string[] {
  if (!Array.isArray(value)) throw new Error(`${path} must be a list of strings`);
  return value.map((item: unknown, index) => {
    if (typeof item !== "string") throw new Error(`${path}[${index}] must be a string`);
    return item;
  });
}

/** Validate the published input declaration; an empty value means no declared input. */
export function decodeWorkflowInputDeclaration(
  raw: string,
): WorkflowInputDeclaration | undefined {
  if (!raw) return undefined;
  const value: unknown = JSON.parse(raw);
  if (!isUnknownRecord(value)) throw new Error("input declaration must be an object");
  for (const key of Object.keys(value)) {
    if (!["type_name", "json_schema", "consumer_node_ids"].includes(key)) {
      throw new Error(`input declaration.${key} is not supported`);
    }
  }
  if (typeof value.type_name !== "string" || !value.type_name) {
    throw new Error("input declaration.type_name must be a non-empty string");
  }
  const jsonSchema = parseNullableSchema(value.json_schema, "input declaration.json_schema");
  if (jsonSchema === null) throw new Error("input declaration.json_schema must be an object");
  return {
    typeName: value.type_name,
    jsonSchema,
    consumerNodeIds: stringList(value.consumer_node_ids, "input declaration.consumer_node_ids"),
  };
}

/** List the declared fields in schema order with their types, defaults and descriptions. */
export function workflowInputFields(
  declaration: WorkflowInputDeclaration,
): WorkflowInputField[] {
  const { jsonSchema } = declaration;
  return modelFields(jsonSchema, jsonSchema.$defs ?? {}, []);
}

/**
 * Pair each declared field with the value the run used. This is the one rule for
 * which fields are defaults on every host: a field the caller did not supply took
 * its declared default. A validated record (the local operator) holds that default;
 * a submitted record (`input.submitted`, Delta) holds only the supplied fields, so a
 * missing field shows the declared default, or "computed" for a `default_factory`.
 */
export function runInputFields(
  declaration: WorkflowInputDeclaration,
  input: RunInputMsg,
): RunInputField[] {
  const values = parseJsonValue(JSON.parse(input.valuesJson), "run input");
  if (values === null || typeof values !== "object" || Array.isArray(values)) {
    throw new Error("run input must be an object");
  }
  const supplied = new Set(input.suppliedFields);
  return workflowInputFields(declaration).map((field): RunInputField => {
    if (Object.hasOwn(values, field.name)) {
      return {
        ...field,
        value: values[field.name],
        source: supplied.has(field.name) ? "supplied" : "default",
      };
    }
    if (input.submitted && !supplied.has(field.name)) {
      if (field.default.kind === "value") {
        return { ...field, value: field.default.value, source: "default" };
      }
      if (field.default.kind === "computed") return { ...field, source: "computed" };
    }
    return { ...field, source: "unrecorded" };
  });
}

/**
 * A run input recorded as the caller submitted it, for hosts that keep the submitted
 * object rather than the validated one: every key is a supplied field.
 */
export function submittedRunInput(
  values: Record<string, unknown>,
  { redacted = false }: { redacted?: boolean } = {},
): RunInputMsg {
  return {
    valuesJson: JSON.stringify(values),
    suppliedFields: Object.keys(values),
    submitted: true,
    ...(redacted ? { redacted } : {}),
  };
}

/** Short, single-line rendering of a value for graph cards and tables. */
export function formatInputValue(value: JsonValue): string {
  return JSON.stringify(value);
}

const TRIGGER_LABELS: Record<TriggerKind, string> = {
  manual: "Started manually",
  scheduled: "Started by its cron schedule",
  webhook: "Started by a webhook",
  api: "Started through the API",
};

/** Describe how a run started, from its summary's `triggeredBy`. */
export function runTriggerLabel(triggeredBy: TriggerKind): string {
  return TRIGGER_LABELS[triggeredBy];
}

/** What a workflow's cron runs start with, or undefined when it has no schedule. */
export function scheduledInputNote(workflow: FlowInfoMsg): string | undefined {
  if (!workflow.cron) return undefined;
  if (!workflow.scheduleEnabled) return `Scheduled runs (${workflow.cron}) are off`;
  return workflow.scheduleInput
    ? `Scheduled runs (${workflow.cron}) use the schedule's input`
    : `Scheduled runs (${workflow.cron}) use these defaults`;
}

/**
 * Starting input for a workflow's runs, or undefined when the workflow declares
 * no input fields. It holds only values someone set (the schedule's stored input),
 * never schema defaults: those don't always validate back as supplied values (a
 * `None` default, a masked secret), and the run should record them as defaults.
 */
export function workflowInputDraft(workflow: FlowInfoMsg): Record<string, unknown> | undefined {
  if (!workflow.inputSchemaJson) return undefined;
  const schema: unknown = JSON.parse(workflow.inputSchemaJson);
  if (!isUnknownRecord(schema)) throw new Error("Workflow input schema must be a JSON object");
  const properties = schema.properties;
  if (!isUnknownRecord(properties) || Object.keys(properties).length === 0) return undefined;
  return { ...workflow.scheduleInput };
}

/** Placeholder for a field whose default is computed when each run starts. */
export const COMPUTED_DEFAULT_PLACEHOLDER = "computed per run";

/** What a field's empty box shows: its default, so the value it falls back to is visible. */
export function defaultPlaceholder(field: WorkflowInputField): string {
  if (field.default.kind === "computed") return COMPUTED_DEFAULT_PLACEHOLDER;
  if (field.default.kind === "required") return "";
  const value = field.default.value;
  return typeof value === "string" && acceptsString(field.schema)
    ? value
    : JSON.stringify(value);
}

/** Whether a field takes text, so the popup can store what is typed as a plain string. */
export function acceptsString(schema: Schema): boolean {
  if (typeof schema === "boolean") return schema;
  if (schema.enum) return schema.enum.every((item) => typeof item === "string");
  if (schema.anyOf) return schema.anyOf.some(acceptsString);
  if (Array.isArray(schema.type)) return schema.type.includes("string");
  return schema.type === "string";
}

/** How a draft value shows in the popup: text as typed, anything else as JSON, null as empty. */
export function inputFieldText(value: unknown, field: WorkflowInputField): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "string" && acceptsString(field.schema)) return value;
  return JSON.stringify(value);
}

/** The names of the files an input value refers to, or undefined when it holds none. */
export function inputValueFileNames(value: unknown): string[] | undefined {
  const name = (item: unknown): string | undefined => {
    if (!isUnknownRecord(item) || item.kind !== "file") return undefined;
    if (typeof item.name === "string") return item.name;
    return isUnknownRecord(item.ref) && typeof item.ref.path === "string"
      ? item.ref.path
      : undefined;
  };
  const single = name(value);
  if (single !== undefined) return [single];
  if (!Array.isArray(value) || value.length === 0) return undefined;
  const names = value.map(name);
  return names.every((item) => item !== undefined) ? names : undefined;
}

/** How many models deep the run form nests sub-forms; deeper models are edited as JSON. */
const MAX_NESTED_MODEL_DEPTH = 4;

/** A model's fields in schema order; `path` is where the model sits in the input. */
function modelFields(
  model: JsonSchema,
  definitions: Record<string, Schema>,
  path: string[],
): WorkflowInputField[] {
  return Object.entries(model.properties ?? {}).map(([name, schema]) => {
    const property = typeof schema === "boolean" ? undefined : schema;
    const fieldDefault: FieldDefault =
      model.required?.includes(name) === true
        ? { kind: "required" }
        : property !== undefined && property.default !== undefined
          ? { kind: "value", value: property.default }
          : { kind: "computed" };
    const fieldPath = [...path, name];
    // Only top-level fields pick files: the upload attaches a file to a field by name.
    const file = path.length === 0 ? fileFieldKind(schema, definitions) : undefined;
    const nested =
      file === undefined && fieldPath.length <= MAX_NESTED_MODEL_DEPTH
        ? fieldModel(schema, definitions)
        : undefined;
    return {
      name,
      typeLabel: schemaTypeLabel(schema, name),
      default: fieldDefault,
      description: property?.description,
      schema,
      file,
      ...(path.length > 0 ? { path: fieldPath } : {}),
      ...(nested ? { model: { fields: modelFields(nested, definitions, fieldPath) } } : {}),
    };
  });
}

/**
 * The model a field takes, looking through a `| None` and a lone `allOf` wrapper and
 * resolving a local `$ref`; undefined for anything else, such as a union of models or
 * a `dict`, which the form edits as JSON.
 */
function fieldModel(
  schema: Schema,
  definitions: Record<string, Schema>,
): JsonSchema | undefined {
  if (typeof schema === "boolean") return undefined;
  const alternatives = schema.anyOf ?? schema.oneOf;
  if (alternatives) {
    const values = alternatives.filter(
      (option) => typeof option === "boolean" || option.type !== "null",
    );
    return values.length === 1 ? fieldModel(values[0], definitions) : undefined;
  }
  if (schema.allOf) {
    return schema.allOf.length === 1 ? fieldModel(schema.allOf[0], definitions) : undefined;
  }
  if (schema.$ref !== undefined) {
    if (!schema.$ref.startsWith("#/$defs/")) return undefined;
    const target = definitions[referenceName(schema.$ref)];
    return target === undefined || typeof target === "boolean" || target.$ref !== undefined
      ? undefined
      : fieldModel(target, definitions);
  }
  const isObject = schema.type === "object" || schema.type === undefined;
  const properties = schema.properties;
  return isObject && properties !== undefined && Object.keys(properties).length > 0
    ? schema
    : undefined;
}

/** Where a field's value sits in the input: its path, or its name at the top level. */
export function inputFieldPath(field: WorkflowInputField): string[] {
  return field.path ?? [field.name];
}
