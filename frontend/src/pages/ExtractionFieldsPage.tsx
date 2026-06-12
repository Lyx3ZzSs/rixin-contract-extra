import { useCallback, useEffect, useMemo, useState } from "react";
import { Plus, RotateCcw, Save, Search, Trash2, X } from "lucide-react";

import type { FieldDefinitionItem } from "../lib/api";
import {
  readExtractionFieldLibrary,
  createExtractionField,
  updateExtractionField,
  deleteExtractionField,
  resetExtractionFieldLibrary,
} from "../lib/extractionFieldLibrary";

type FieldDraft = Pick<FieldDefinitionItem, "field_name" | "description">;

const EMPTY_DRAFT: FieldDraft = {
  field_name: "",
  description: "",
};

export function ExtractionFieldsPage() {
  const [fields, setFields] = useState<FieldDefinitionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [editingFieldKey, setEditingFieldKey] = useState("");
  const [isAdding, setIsAdding] = useState(false);
  const [draft, setDraft] = useState<FieldDraft>(EMPTY_DRAFT);

  const loadFields = useCallback(async () => {
    setLoading(true);
    try {
      const items = await readExtractionFieldLibrary();
      setFields(items);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadFields();
  }, [loadFields]);

  const filteredFields = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    return fields.filter((field) => {
      return (
        !keyword ||
        field.field_name.toLowerCase().includes(keyword) ||
        field.description.toLowerCase().includes(keyword)
      );
    });
  }, [fields, query]);

  function updateDraft(key: keyof FieldDraft, value: string) {
    setDraft((currentDraft) => ({ ...currentDraft, [key]: value }));
  }

  function startAdd() {
    setIsAdding(true);
    setEditingFieldKey("");
    setDraft(EMPTY_DRAFT);
  }

  function startEdit(field: FieldDefinitionItem) {
    setIsAdding(false);
    setEditingFieldKey(field.field_key);
    setDraft({ field_name: field.field_name, description: field.description });
  }

  function cancelDraft() {
    setIsAdding(false);
    setEditingFieldKey("");
    setDraft(EMPTY_DRAFT);
  }

  async function saveDraft() {
    const name = draft.field_name.trim();
    const description = draft.description.trim();
    if (!name) return;
    try {
      if (isAdding) {
        await createExtractionField(name, description);
      } else if (editingFieldKey) {
        await updateExtractionField(editingFieldKey, { name, description });
      }
    } catch {
      // ignore
    }
    cancelDraft();
    await loadFields();
  }

  async function removeField(fieldKey: string) {
    try {
      await deleteExtractionField(fieldKey);
    } catch {
      // ignore — loadFields will still fetch latest from server
    }
    if (editingFieldKey === fieldKey) cancelDraft();
    await loadFields();
  }

  async function restoreDefaults() {
    try {
      await resetExtractionFieldLibrary();
    } catch {
      // ignore
    }
    setQuery("");
    cancelDraft();
    await loadFields();
  }

  async function toggleRequired(field: FieldDefinitionItem) {
    try {
      await updateExtractionField(field.field_key, { required: !field.required });
    } catch {
      // ignore
    }
    await loadFields();
  }

  const requiredCount = fields.filter(f => f.required).length;

  return (
    <section className="field-library-workspace" aria-labelledby="field-library-title">
      <header className="field-library-header">
        <div>
          <span>合同智能提取</span>
          <h1 id="field-library-title">提取字段管理</h1>
        </div>
        <div className="field-library-summary" aria-label="字段库统计">
          <span>
            <b>{fields.length}</b>
            字段
          </span>
          <span>
            <b>{requiredCount}</b>
            启用字段
          </span>
        </div>
      </header>

      <div className="field-library-panel">
        <div className="field-library-toolbar">
          <label className="field-library-search">
            <Search aria-hidden="true" />
            <input
              aria-label="搜索提取字段"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索字段名称或描述"
            />
          </label>
          <button className="field-library-secondary" type="button" onClick={restoreDefaults}>
            <RotateCcw aria-hidden="true" />
            恢复默认字段
          </button>
          <button className="field-library-primary" type="button" onClick={startAdd}>
            <Plus aria-hidden="true" />
            新增字段
          </button>
        </div>

        {loading && <div style={{ padding: "1rem", color: "#888" }}>加载中...</div>}

        {isAdding && (
          <FieldEditor
            draft={draft}
            title="新增提取字段"
            onCancel={cancelDraft}
            onChange={updateDraft}
            onSave={saveDraft}
          />
        )}

        <div className="field-library-table" aria-label="提取字段库">
          <div className="field-library-row header">
            <strong>字段名称</strong>
            <strong>字段描述</strong>
            <strong>是否启用</strong>
            <strong>操作</strong>
          </div>
          {filteredFields.length === 0 && !loading ? (
            <div className="field-library-empty">
              <strong>没有匹配字段</strong>
              <span>调整搜索或筛选条件后再试。</span>
            </div>
          ) : (
            filteredFields.map((field) =>
              editingFieldKey === field.field_key ? (
                <FieldEditor
                  key={field.field_key}
                  draft={draft}
                  title={`编辑 ${field.field_name}`}
                  onCancel={cancelDraft}
                  onChange={updateDraft}
                  onSave={saveDraft}
                />
              ) : (
                <div className="field-library-row" key={field.field_key}>
                  <strong title={field.field_name}>{field.field_name}</strong>
                  <span title={field.description}>{field.description || "-"}</span>
                  <button
                    className={field.required ? "field-switch on" : "field-switch"}
                    type="button"
                    aria-label={`${field.field_name}是否必填`}
                    aria-pressed={field.required}
                    onClick={() => toggleRequired(field)}
                  />
                  <div className="field-library-actions">
                    <button type="button" onClick={() => startEdit(field)}>
                      编辑
                    </button>
                    <button type="button" onClick={() => removeField(field.field_key)}>
                      <Trash2 aria-hidden="true" />
                      删除
                    </button>
                  </div>
                </div>
              ),
            )
          )}
        </div>
      </div>
    </section>
  );
}

function FieldEditor({
  draft,
  title,
  onCancel,
  onChange,
  onSave,
}: {
  draft: FieldDraft;
  title: string;
  onCancel: () => void;
  onChange: (key: keyof FieldDraft, value: string) => void;
  onSave: () => void;
}) {
  return (
    <div className="field-library-editor" aria-label={title}>
      <strong>{title}</strong>
      <label>
        <span>字段名称</span>
        <input
          aria-label="字段名称"
          value={draft.field_name}
          onChange={(event) => onChange("field_name", event.target.value)}
          placeholder="例如 合同金额"
        />
      </label>
      <label>
        <span>字段描述</span>
        <input
          aria-label="字段描述"
          value={draft.description}
          onChange={(event) => onChange("description", event.target.value)}
          placeholder="用于提示模型抽取的字段含义"
        />
      </label>
      <div className="field-library-editor-actions">
        <button type="button" onClick={onSave} disabled={!draft.field_name.trim()}>
          <Save aria-hidden="true" />
          保存
        </button>
        <button type="button" onClick={onCancel}>
          <X aria-hidden="true" />
          取消
        </button>
      </div>
    </div>
  );
}
