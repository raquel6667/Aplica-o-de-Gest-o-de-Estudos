DROP DATABASE IF EXISTS plataforma_estudo;
CREATE DATABASE plataforma_estudo
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE plataforma_estudo;

CREATE TABLE administrador (
    cod_administrador INT UNSIGNED NOT NULL AUTO_INCREMENT,
    username VARCHAR(50) NOT NULL,
    email VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    nome VARCHAR(100) NOT NULL DEFAULT 'Administrador Principal',
    nivel_acesso ENUM('super_admin','admin','moderador') NOT NULL DEFAULT 'admin',
    tentativas_falhadas TINYINT UNSIGNED NOT NULL DEFAULT 0,
    bloqueado_ate DATETIME NULL,
    ultimo_login DATETIME NULL,
    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (cod_administrador),
    UNIQUE KEY uq_administrador_username (username),
    UNIQUE KEY uq_administrador_email (email)
) ENGINE=InnoDB;

CREATE TABLE disciplinas (
    cod_disciplina INT UNSIGNED NOT NULL AUTO_INCREMENT,
    nome VARCHAR(150) NOT NULL,
    descricao VARCHAR(500) NULL,
    estado ENUM('ativa', 'pendente', 'inativa') NOT NULL DEFAULT 'ativa',
    cod_administrador INT UNSIGNED NULL,
    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (cod_disciplina),
    UNIQUE KEY uq_disciplinas_nome (nome),
    KEY idx_disciplinas_nome (nome),
    CONSTRAINT fk_disciplinas_administrador
        FOREIGN KEY (cod_administrador) REFERENCES administrador (cod_administrador)
        ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB;

INSERT INTO administrador (username, email, password_hash, nome, nivel_acesso)
VALUES ('admin', 'admin@plataforma.com', 'admin2026', 'Administrador Principal', 'super_admin');