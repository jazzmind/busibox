-- RBAC & metadata schema
create extension if not exists "uuid-ossp";

create table if not exists roles (
  id uuid primary key default uuid_generate_v4(),
  name text unique not null
);

create table if not exists users (
  id uuid primary key default uuid_generate_v4(),
  email text unique not null
);

create table if not exists user_roles (
  user_id uuid references users(id) on delete cascade,
  role_id uuid references roles(id) on delete cascade,
  primary key (user_id, role_id)
);

create table if not exists files (
  id uuid primary key default uuid_generate_v4(),
  owner_id uuid references users(id),
  role_id uuid references roles(id) not null,
  filename text not null,
  mime_type text,
  bytes bigint not null,
  bucket text not null,
  key text not null,
  sha256 bytea not null,
  status text default 'pending',
  created_at timestamptz default now()
);

create table if not exists chunks (
  id bigserial primary key,
  file_id uuid references files(id) on delete cascade,
  role_id uuid references roles(id) not null,
  chunk_index int not null,
  byte_start int,
  byte_end int,
  text_preview text,
  vector_id bigint,
  created_at timestamptz default now()
);

alter table files enable row level security;
alter table chunks enable row level security;

drop policy if exists files_role_access on files;
create policy files_role_access on files
  using (exists (select 1 from user_roles ur where ur.user_id = current_setting('app.user_id', true)::uuid
                                          and ur.role_id = files.role_id));

drop policy if exists chunks_role_access on chunks;
create policy chunks_role_access on chunks
  using (exists (select 1 from user_roles ur where ur.user_id = current_setting('app.user_id', true)::uuid
                                          and ur.role_id = chunks.role_id));
