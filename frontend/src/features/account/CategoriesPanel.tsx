/**
 * Category management.
 *
 * Categories are created inline during review with no drawn management
 * screen (see `app/schemas/account.py`'s module docstring) — this closes
 * that gap: list, create, rename, merge and delete.
 */

import { useState } from "react";
import { ApiError } from "../../api/client";
import type { CategoryResponse } from "../../api/types";
import {
  useCategories,
  useCreateCategory,
  useDeleteCategory,
  useMergeCategory,
  useUpdateCategory,
} from "./queries";

export function CategoriesPanel() {
  const categories = useCategories();
  const [error, setError] = useState<string | null>(null);

  if (categories.isLoading) return <p className="muted">Loading categories…</p>;
  if (categories.error) {
    return (
      <p className="error" role="alert">
        {categories.error instanceof ApiError
          ? categories.error.message
          : "Could not load categories."}
      </p>
    );
  }

  return (
    <div className="stack">
      <CreateCategoryForm onError={setError} />
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <table className="table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Kind</th>
            <th>Transactions</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {(categories.data ?? []).map((category) => (
            <CategoryRow
              key={category.id}
              category={category}
              allCategories={categories.data ?? []}
              onError={setError}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CreateCategoryForm({ onError }: { onError: (message: string | null) => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"income" | "expense" | "transfer">("expense");
  const create = useCreateCategory();

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    onError(null);
    try {
      await create.mutateAsync({ name, kind });
      setName("");
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "Could not create the category.");
    }
  }

  return (
    <form className="field-row" onSubmit={handleSubmit} style={{ alignItems: "flex-end" }}>
      <div className="field">
        <label htmlFor="new-category-name">New category</label>
        <input
          id="new-category-name"
          required
          maxLength={100}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="new-category-kind">Kind</label>
        <select
          id="new-category-kind"
          value={kind}
          onChange={(event) => setKind(event.target.value as typeof kind)}
        >
          <option value="expense">Expense</option>
          <option value="income">Income</option>
          <option value="transfer">Transfer</option>
        </select>
      </div>
      <button type="submit" className="button--primary" disabled={create.isPending}>
        Add
      </button>
    </form>
  );
}

function CategoryRow({
  category,
  allCategories,
  onError,
}: {
  category: CategoryResponse;
  allCategories: CategoryResponse[];
  onError: (message: string | null) => void;
}) {
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(category.name);
  const [merging, setMerging] = useState(false);
  const [mergeTarget, setMergeTarget] = useState("");

  const update = useUpdateCategory();
  const merge = useMergeCategory();
  const remove = useDeleteCategory();

  const mergeCandidates = allCategories.filter((other) => other.id !== category.id);

  async function handleRename(event: React.FormEvent) {
    event.preventDefault();
    onError(null);
    try {
      await update.mutateAsync({ categoryId: category.id, body: { name } });
      setRenaming(false);
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "Could not rename the category.");
    }
  }

  async function handleMerge() {
    onError(null);
    try {
      await merge.mutateAsync({
        categoryId: category.id,
        body: { into_category_id: mergeTarget },
      });
      setMerging(false);
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "Could not merge the category.");
    }
  }

  async function handleDelete() {
    onError(null);
    if (!window.confirm(`Delete "${category.name}"? This only works while it is empty.`)) {
      return;
    }
    try {
      await remove.mutateAsync(category.id);
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "Could not delete the category.");
    }
  }

  return (
    <>
      <tr>
        <td>
          {renaming ? (
            <form className="field-row" onSubmit={handleRename}>
              <input
                value={name}
                maxLength={100}
                onChange={(event) => setName(event.target.value)}
              />
              <button type="submit" className="button--primary">
                Save
              </button>
              <button type="button" onClick={() => setRenaming(false)}>
                Cancel
              </button>
            </form>
          ) : (
            <>
              {category.name}{" "}
              {category.is_system && <span className="badge badge--muted">system</span>}
            </>
          )}
        </td>
        <td>{category.kind}</td>
        <td>{category.row_count}</td>
        <td>
          <div className="button-row">
            {!renaming && (
              <button type="button" onClick={() => setRenaming(true)}>
                Rename
              </button>
            )}
            {mergeCandidates.length > 0 && (
              <button type="button" onClick={() => setMerging((value) => !value)}>
                Merge into…
              </button>
            )}
            {category.row_count === 0 && !category.is_system && (
              <button type="button" className="button--danger" onClick={() => void handleDelete()}>
                Delete
              </button>
            )}
          </div>
        </td>
      </tr>
      {merging && (
        <tr>
          <td colSpan={4}>
            <div className="field-row" style={{ alignItems: "flex-end" }}>
              <div className="field">
                <label htmlFor={`merge-target-${category.id}`}>
                  Move {category.row_count} transaction(s) into
                </label>
                <select
                  id={`merge-target-${category.id}`}
                  value={mergeTarget}
                  onChange={(event) => setMergeTarget(event.target.value)}
                >
                  <option value="">Choose a category…</option>
                  {mergeCandidates.map((candidate) => (
                    <option key={candidate.id} value={candidate.id}>
                      {candidate.name}
                    </option>
                  ))}
                </select>
              </div>
              <button
                type="button"
                className="button--danger"
                disabled={!mergeTarget || merge.isPending}
                onClick={() => void handleMerge()}
              >
                Merge and delete "{category.name}"
              </button>
              <button type="button" onClick={() => setMerging(false)}>
                Cancel
              </button>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
