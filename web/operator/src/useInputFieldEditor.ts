import { isUnknownRecord } from "./guards";
import { parseRunInput } from "./RunControls";
import type { InputDraft } from "./useInputDraft";
import {
  acceptsString,
  inputFieldPath,
  inputFieldText,
  type WorkflowInputField,
} from "./workflowInput";

export interface InputFieldEditor {
  /** Why the fields cannot be edited right now. */
  error?: string;
  /** The draft's file picks and the field values they replace. */
  draft: InputDraft;
  value: (field: WorkflowInputField) => unknown;
  text: (field: WorkflowInputField) => string;
  invalid: (field: WorkflowInputField) => boolean;
  update: (field: WorkflowInputField, text: string) => void;
}

/** The value at a path in the input, or undefined when a step on the way is missing. */
function valueAt(values: Record<string, unknown> | undefined, path: string[]): unknown {
  let current: unknown = values;
  for (const key of path) {
    if (!isUnknownRecord(current)) return undefined;
    current = current[key];
  }
  return current;
}

/**
 * A copy of `values` with the value at `path` set, or left out when `value` is
 * undefined. A model left with no fields is left out too, so an untouched nested
 * field takes its default like any other empty field.
 */
function withValueAt(
  values: Record<string, unknown>,
  path: string[],
  value: unknown,
): Record<string, unknown> {
  const [key, ...rest] = path;
  const next = { ...values };
  const child =
    rest.length === 0
      ? value
      : withValueAt(isUnknownRecord(values[key]) ? values[key] : {}, rest, value);
  if (child === undefined || (rest.length > 0 && Object.keys(child as object).length === 0)) {
    delete next[key];
  } else {
    next[key] = child;
  }
  return next;
}

/**
 * Edit one field of the shared input draft at a time; a model's fields edit their
 * place in the nested value. Text fields store what is typed; other fields take
 * JSON. An empty field leaves the field out of the input.
 */
export function useInputFieldEditor(draft: InputDraft): InputFieldEditor {
  const { pending, setPending } = draft;
  let values: Record<string, unknown> | undefined;
  let error: string | undefined;
  try {
    values = parseRunInput(draft.text);
  } catch {
    error = "The input draft is not a JSON object.";
  }
  const key = (field: WorkflowInputField) => inputFieldPath(field).join(".");
  return {
    error,
    draft,
    value: (field) => valueAt(values, inputFieldPath(field)),
    text: (field) =>
      pending[key(field)] ?? inputFieldText(valueAt(values, inputFieldPath(field)), field),
    invalid: (field) => Object.hasOwn(pending, key(field)),
    update: (field, text) => {
      if (!values) return;
      const name = key(field);
      let value: unknown;
      if (text.trim() === "") {
        value = undefined;
      } else if (acceptsString(field.schema)) {
        value = text;
      } else {
        try {
          value = JSON.parse(text);
        } catch {
          setPending((current) => ({ ...current, [name]: text }));
          return;
        }
      }
      setPending(({ [name]: _, ...rest }) => rest);
      draft.setText(JSON.stringify(withValueAt(values, inputFieldPath(field), value), null, 2));
    },
  };
}
