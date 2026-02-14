---
title: "App Data Schemas"
category: "developer"
order: 144
description: "Schema format for structured app data in data-api"
published: true
---

# App Data Schemas

**Created**: 2026-02-08  
**Updated**: 2026-02-08  
**Status**: Active  
**Category**: reference

This document describes the schema format for structured app data stored in the Busibox data-api. Schemas define field types, display hints, and relationships between data documents.

## Overview

Apps that store structured data in the data-api can define schemas that:

1. **Define field types** - Specify data types and validation rules
2. **Provide display hints** - Control how fields appear in forms and lists
3. **Declare relationships** - Link documents together for navigation

The AI Portal uses these schemas to render data views with proper formatting, validation, and navigation between related records.

## Schema Structure

```typescript
import type { AppDataSchema, AppDataRelation } from '@jazzmind/busibox-app';

const mySchema: AppDataSchema = {
  // Field definitions (required)
  fields: {
    id: { type: 'string', required: true, hidden: true },
    name: { type: 'string', required: true, label: 'Name', order: 1 },
    status: { type: 'enum', values: ['active', 'inactive'], label: 'Status', order: 2 },
    // ... more fields
  },
  
  // Display metadata
  displayName: 'My Items',        // Plural name for the document type
  itemLabel: 'Item',              // Singular name for individual records
  sourceApp: 'my-app',            // App identifier
  visibility: 'personal',         // Default visibility: 'personal' | 'shared'
  allowSharing: true,             // Whether items can be shared
  
  // Relationships to other documents
  relations: {
    parent: {
      type: 'belongsTo',
      document: 'my-app-parents',
      foreignKey: 'parentId',
      displayField: 'name',
      label: 'Parent',
    },
    children: {
      type: 'hasMany',
      document: 'my-app-children',
      foreignKey: 'itemId',
      displayField: 'title',
      label: 'Children',
    },
  },
};
```

## Field Types

### Basic Types

| Type | Description | Example |
|------|-------------|---------|
| `string` | Text value | Names, descriptions |
| `integer` | Whole number | Counts, order |
| `number` | Decimal number | Prices, percentages |
| `boolean` | True/false | Flags, toggles |
| `datetime` | ISO date string | Timestamps |
| `array` | List of values | Tags, team members |
| `object` | Nested object | Complex data |
| `enum` | Predefined values | Status, priority |

### Field Definition Options

```typescript
interface AppDataFieldDef {
  type: 'string' | 'integer' | 'number' | 'boolean' | 'array' | 'object' | 'enum' | 'datetime';
  
  // Validation
  required?: boolean;          // Field is required
  values?: string[];           // For enum types: allowed values
  min?: number;                // Minimum value (for numbers)
  max?: number;                // Maximum value (for numbers)
  
  // Display hints
  label?: string;              // Human-readable label
  hidden?: boolean;            // Don't show in list/form (e.g., id)
  multiline?: boolean;         // Use textarea for strings
  readonly?: boolean;          // Cannot edit in form
  order?: number;              // Display order (lower = first)
  placeholder?: string;        // Placeholder text
  
  // Widget override
  widget?: 'text' | 'textarea' | 'select' | 'slider' | 'number' | 'date' | 'checkbox' | 'tags';
}
```

### Widget Types

| Widget | Use Case |
|--------|----------|
| `text` | Single-line text input |
| `textarea` | Multi-line text input |
| `select` | Dropdown for enum fields |
| `slider` | Range input for numbers |
| `number` | Numeric input with spinners |
| `date` | Date picker |
| `checkbox` | Boolean toggle |
| `tags` | Tag input for arrays |

## Relationships

Relationships allow navigation between related data documents. The AI Portal displays these as clickable links.

### Relation Types

| Type | Description | Example |
|------|-------------|---------|
| `belongsTo` | Record references a parent | Task → Project |
| `hasMany` | Record has children in another document | Project → Tasks |

### Relation Definition

```typescript
interface AppDataRelation {
  type: 'hasMany' | 'belongsTo';
  document: string;        // Target document name
  foreignKey: string;      // Field linking records
  displayField?: string;   // Field to show in links (default: 'name' or 'title')
  label?: string;          // UI label for the relation
}
```

### Example: Bidirectional Relations

For Projects and Tasks:

```typescript
// Project schema - has many tasks
const projectSchema: AppDataSchema = {
  fields: { /* ... */ },
  relations: {
    tasks: {
      type: 'hasMany',
      document: 'my-app-tasks',
      foreignKey: 'projectId',
      displayField: 'title',
      label: 'Tasks',
    },
  },
};

// Task schema - belongs to project
const taskSchema: AppDataSchema = {
  fields: {
    projectId: { type: 'string', required: true, hidden: true },
    title: { type: 'string', required: true },
    /* ... */
  },
  relations: {
    project: {
      type: 'belongsTo',
      document: 'my-app-projects',
      foreignKey: 'projectId',
      displayField: 'name',
      label: 'Project',
    },
  },
};
```

## AI Portal Integration

The AI Portal Admin Data view uses schemas to:

1. **Display records** - Uses `displayName`, `itemLabel`, field `label` and `order`
2. **Render forms** - Uses field types and `widget` hints
3. **Navigate relations** - Shows clickable links for `belongsTo` and expandable lists for `hasMany`
4. **Filter by source app** - Groups documents by `sourceApp`

### How It Works

When viewing a data document in AI Portal:

1. **BelongsTo relations**: Shown as inline links on each record
   - Example: Task shows "Project: My Project" as a clickable link

2. **HasMany relations**: Shown as expandable sections
   - Example: Project shows "Tasks (5)" with expandable list
   - Click to view related records

3. **Cross-document navigation**: Links go to the target document with the record highlighted

## Usage in Apps

### 1. Install busibox-app

```bash
npm install @jazzmind/busibox-app
```

### 2. Import Types

```typescript
import type { AppDataSchema, AppDataRelation, AppDataFieldDef } from '@jazzmind/busibox-app';
```

### 3. Define Schemas

```typescript
export const mySchema: AppDataSchema = {
  fields: { /* ... */ },
  displayName: 'My Items',
  itemLabel: 'Item',
  sourceApp: 'my-app',
  relations: { /* ... */ },
};
```

### 4. Create Documents with Schema

```typescript
await dataApiRequest(token, '/data', {
  method: 'POST',
  body: JSON.stringify({
    name: 'my-app-items',
    schema: mySchema,
    visibility: 'personal',
    sourceApp: 'my-app',
  }),
});
```

## Best Practices

1. **Always set `sourceApp`** - Required for AI Portal to group documents
2. **Use meaningful labels** - Helps users understand the data
3. **Set field order** - Controls display order in forms
4. **Hide internal fields** - Use `hidden: true` for IDs and timestamps
5. **Define bidirectional relations** - Both parent and child should have relation definitions
6. **Use `displayField`** - Specify which field to show in relation links

## Related Documentation

- [Data API Reference](03-api.md)
- [@jazzmind/busibox-app](https://www.npmjs.com/package/@jazzmind/busibox-app) — types and client
