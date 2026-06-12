import type { FieldDefinitionItem } from "./api";
import {
  listFieldDefinitions,
  createFieldDefinition as apiCreate,
  updateFieldDefinition as apiUpdate,
  deleteFieldDefinition as apiDelete,
  resetFieldDefinitions as apiReset,
} from "./api";

export type { FieldDefinitionItem as ExtractionFieldDefinition };

export async function readExtractionFieldLibrary(): Promise<FieldDefinitionItem[]> {
  try {
    return await listFieldDefinitions();
  } catch {
    return [];
  }
}

export async function createExtractionField(
  name: string,
  description: string,
): Promise<FieldDefinitionItem> {
  const field_key = `custom-${Date.now().toString(36)}`;
  const item = await apiCreate({
    field_key,
    field_name: name.trim(),
    field_category: "basic",
    description: description.trim(),
    value_type: "string",
    required: false,
    sort_order: Math.floor(Date.now() / 1000),
  });
  return item;
}

export async function updateExtractionField(
  fieldKey: string,
  updates: { name?: string; description?: string; required?: boolean; is_active?: boolean },
): Promise<FieldDefinitionItem> {
  const payload: Record<string, unknown> = {};
  if (updates.name !== undefined) payload.field_name = updates.name;
  if (updates.description !== undefined) payload.description = updates.description;
  if (updates.required !== undefined) payload.required = updates.required;
  if (updates.is_active !== undefined) payload.is_active = updates.is_active;
  const item = await apiUpdate(fieldKey, payload);
  return item;
}

export async function deleteExtractionField(fieldKey: string): Promise<void> {
  await apiDelete(fieldKey);
}

export async function resetExtractionFieldLibrary(): Promise<FieldDefinitionItem[]> {
  const items = await apiReset();
  return items;
}

/** @deprecated Use the async functions above instead. */
export function writeExtractionFieldLibrary(_fields: unknown[]): void {
  // No-op: DB is now the source of truth
}
