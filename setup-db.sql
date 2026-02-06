-- Cria usuário e banco
CREATE USER fastleaks WITH PASSWORD 'senha_segura';
CREATE DATABASE fastleaksdf OWNER fastleaks;

-- Conecta no banco
\c fastleaksdf

-- SQLModel criará tabelas automaticamente no primeiro uso
-- Mas você pode pré-criar índices para performance:
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_documents_sha256 ON documents(sha256);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_iocs_document_id ON iocs(document_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_iocs_type_value ON iocs(ioc_type, value);