import os
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship, joinedload
from google import genai

# -------------------------------------------------------------------
# Configuración de Base de Datos para Producción (Render)
# -------------------------------------------------------------------
DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "postgresql+pg8000://postgres:0811@localhost:5432/bdpersonas"
)

if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if DATABASE_URL and DATABASE_URL.startswith("postgresql://") and "+pg8000" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+pg8000://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# -------------------------------------------------------------------
# Cliente de la IA (Gemini SDK)
# -------------------------------------------------------------------
# Lee automáticamente GEMINI_API_KEY desde las variables de entorno de Render
ai_client = None
if os.getenv("GEMINI_API_KEY"):
    ai_client = genai.Client()


# -------------------------------------------------------------------
# Modelos de la Base de Datos
# -------------------------------------------------------------------
class TipoDocumento(Base):
    __tablename__ = "tipos_documento"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(50), nullable=False, unique=True)


class Ciudad(Base):
    __tablename__ = "ciudades"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(100), nullable=False, unique=True)


class Genero(Base):
    __tablename__ = "generos"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(50), nullable=False, unique=True)


class Persona(Base):
    __tablename__ = "personas"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(100), nullable=False)
    numero_documento = Column(String(20), nullable=False, unique=True)

    tipo_documento_id = Column(Integer, ForeignKey("tipos_documento.id"), nullable=False)
    ciudad_id = Column(Integer, ForeignKey("ciudades.id"), nullable=False)
    genero_id = Column(Integer, ForeignKey("generos.id"), nullable=False)

    tipo_documento = relationship("TipoDocumento")
    ciudad = relationship("Ciudad")
    genero = relationship("Genero")


# Crear tablas automáticamente al iniciar
Base.metadata.create_all(bind=engine)


# Dependencia de sesión
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


app = FastAPI(title="API de Personas con PostgreSQL e IA")


@app.on_event("startup")
def startup_populate_db():
    """Poblar datos iniciales si los catálogos están vacíos."""
    db = SessionLocal()
    try:
        if not db.query(TipoDocumento).first():
            td_cc = TipoDocumento(nombre="Cédula de Ciudadanía")
            td_ti = TipoDocumento(nombre="Tarjeta de Identidad")
            td_ce = TipoDocumento(nombre="Cédula de Extranjería")
            db.add_all([td_cc, td_ti, td_ce])

            c_bog = Ciudad(nombre="Bogotá")
            c_med = Ciudad(nombre="Medellín")
            c_cal = Ciudad(nombre="Cali")
            db.add_all([c_bog, c_med, c_cal])

            g_m = Genero(nombre="Masculino")
            g_f = Genero(nombre="Femenino")
            g_o = Genero(nombre="Otro")
            db.add_all([g_m, g_f, g_o])

            db.commit()

            p1 = Persona(
                nombre="Carlos Gómez",
                numero_documento="1012345678",
                tipo_documento_id=td_cc.id,
                ciudad_id=c_bog.id,
                genero_id=g_m.id,
            )
            p2 = Persona(
                nombre="María Rodríguez",
                numero_documento="1098765432",
                tipo_documento_id=td_cc.id,
                ciudad_id=c_med.id,
                genero_id=g_f.id,
            )
            db.add_all([p1, p2])
            db.commit()
    except Exception as e:
        db.rollback()
        print(f"Error al inicializar datos: {e}")
    finally:
        db.close()


# -------------------------------------------------------------------
# Endpoints Existentes
# -------------------------------------------------------------------
@app.get("/tipos-documento")
def get_tipos_documento(db: Session = Depends(get_db)):
    return db.query(TipoDocumento).all()


@app.get("/ciudades")
def get_ciudades(db: Session = Depends(get_db)):
    return db.query(Ciudad).all()


@app.get("/generos")
def get_generos(db: Session = Depends(get_db)):
    return db.query(Genero).all()


@app.get("/personas")
def get_personas(db: Session = Depends(get_db)):
    # Uso de joinedload para traer las relaciones en una sola consulta SQL
    personas = db.query(Persona).options(
        joinedload(Persona.tipo_documento),
        joinedload(Persona.ciudad),
        joinedload(Persona.genero)
    ).all()
    
    return [
        {
            "id": p.id,
            "nombre": p.nombre,
            "numero_documento": p.numero_documento,
            "tipo_documento": p.tipo_documento.nombre if p.tipo_documento else None,
            "ciudad": p.ciudad.nombre if p.ciudad else None,
            "genero": p.genero.nombre if p.genero else None,
        }
        for p in personas
    ]


# -------------------------------------------------------------------
# Nuevo Endpoint: Análisis de datos con IA
# -------------------------------------------------------------------
@app.get("/personas/analisis")
def get_personas_analisis(db: Session = Depends(get_db)):
    """Obtiene los datos de la BD y los envía al modelo de IA para generar un informe."""
    if not ai_client:
        raise HTTPException(
            status_code=500, 
            detail="GEMINI_API_KEY no está configurada en las variables de entorno."
        )

    # 1. Recuperar los datos formateados
    datos_personas = get_personas(db)

    if not datos_personas:
        return {"mensaje": "No hay registros de personas para analizar."}

    # 2. Construir el prompt estructurado
    prompt = f"""
    Eres un analista de datos. Analiza el siguiente listado de personas proveniente de la base de datos 
    y genera un informe descriptivo y conciso en formato Markdown:

    - Muestra un resumen general de la cantidad total de personas.
    - Distribución por ciudad y género.
    - Tipos de documentos representados.
    - Cualquier hallazgo relevante sobre los datos.

    Datos de entrada (JSON):
    {datos_personas}
    """

    try:
        # 3. Solicitar la generación del contenido al modelo
        response = ai_client.models.generate_content(
            model='gemini-3.8-flash',
            contents=prompt
        )

        return {
            "total_registros": len(datos_personas),
            "analisis": response.text,
            "datos_analizados": datos_personas
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generando el análisis: {str(e)}")