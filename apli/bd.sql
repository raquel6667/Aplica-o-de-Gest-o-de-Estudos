DROP DATABASE IF EXISTS plataforma_estudo;
CREATE DATABASE plataforma_estudo
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE plataforma_estudo;

-- US1.01: Tabela do Administrador
CREATE TABLE administrador (
    cod_administrador INT UNSIGNED NOT NULL AUTO_INCREMENT,
    username VARCHAR(50) NOT NULL,
    email VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    nome VARCHAR(100) NOT NULL DEFAULT 'Administrador Principal',
    nivel_acesso ENUM('super_admin','admin') NOT NULL DEFAULT 'admin',
    ultimo_login DATETIME NULL,
    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (cod_administrador),
    UNIQUE KEY uq_administrador_username (username),
    UNIQUE KEY uq_administrador_email (email)
) ENGINE=InnoDB;

-- US2.01: Tabela de Disciplinas (com Código, Nome, Descrição e Estado)
CREATE TABLE disciplinas (
    cod_disciplina INT UNSIGNED NOT NULL AUTO_INCREMENT,
    codigo VARCHAR(20) NOT NULL,
    nome VARCHAR(150) NOT NULL,
    descricao VARCHAR(500) NULL,
    estado ENUM('ativa', 'pendente', 'inativa') NOT NULL DEFAULT 'ativa',
    cod_administrador INT UNSIGNED NULL,
    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (cod_disciplina),
    UNIQUE KEY uq_disciplinas_codigo (codigo),
    UNIQUE KEY uq_disciplinas_nome (nome),
    KEY idx_disciplinas_nome (nome),
    CONSTRAINT fk_disciplinas_administrador
        FOREIGN KEY (cod_administrador) REFERENCES administrador (cod_administrador)
        ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB;

-- Administrador por defeito (Credenciais: admin / admin2026)
INSERT INTO administrador (username, email, password_hash, nome, nivel_acesso)
VALUES ('admin', 'admin@plataforma.com', 'admin2026', 'Administrador Principal', 'super_admin');