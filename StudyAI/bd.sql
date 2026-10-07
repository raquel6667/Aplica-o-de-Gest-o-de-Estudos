-- 1. Criar a base de dados
CREATE DATABASE IF NOT EXISTS aplicacao;
USE aplicacao;

-- 2. Criar tabela Administrador
CREATE TABLE Administrador (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) NOT NULL,
    email VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    nome VARCHAR(100) NOT NULL,
    nivel_acesso VARCHAR(50) DEFAULT 'admin'
);

-- 3. Criar tabela Disciplinas
CREATE TABLE Disciplinas (
    ID_Disciplinas INT AUTO_INCREMENT PRIMARY KEY,
    codigo VARCHAR(20) NOT NULL UNIQUE, -- Código definido pelo administrador (ex: MAT101)
    nome VARCHAR(100) NOT NULL,
    descricao VARCHAR(500) NULL,
    estado ENUM('ativa', 'pendente', 'inativa') NOT NULL DEFAULT 'ativa',
    cod_administrador INT NULL,
    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_disciplinas_administrador FOREIGN KEY (cod_administrador) REFERENCES Administrador(id) ON DELETE SET NULL
);



INSERT INTO Administrador (username, email, password_hash, nome, nivel_acesso) 
VALUES (
    'admin', 
    'novo_admin@plataforma.com', 
    '123', -- Password em texto simples (para testes)
    'Nome do Administrador', 
    'admin'
);
-- Exemplo de inserção de disciplina incluindo o código criado pelo administrador (ex: 'MAT101')
INSERT INTO Disciplinas (codigo, nome, descricao, estado, cod_administrador) 
VALUES ('MAT101', 'Matemática A', 'Álgebra, Geometria e Cálculo Diferencial.', 'ativa', 1);