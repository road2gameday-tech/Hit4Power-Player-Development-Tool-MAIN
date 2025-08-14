from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Float, Boolean, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base

class Instructor(Base):
    __tablename__ = "instructors"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False, default="Coach")
    login_code = Column(String(64), unique=True, index=True, nullable=False)

    favorites = relationship("InstructorFavorite", back_populates="instructor")

class Player(Base):
    __tablename__ = "players"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False)
    age = Column(Integer, nullable=False, default=12)
    login_code = Column(String(64), unique=True, index=True, nullable=False)
    phone = Column(String(32), nullable=True)
    image_path = Column(String(255), nullable=True)

    metrics = relationship("Metric", back_populates="player", cascade="all, delete-orphan")
    notes = relationship("Note", back_populates="player", cascade="all, delete-orphan")
    drills = relationship("DrillAssignment", back_populates="player", cascade="all, delete-orphan")

class Metric(Base):
    __tablename__ = "metrics"
    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    date = Column(DateTime, default=datetime.utcnow, index=True)
    exit_velocity = Column(Float, nullable=True)
    launch_angle = Column(Float, nullable=True)
    spin_rate = Column(Float, nullable=True)

    player = relationship("Player", back_populates="metrics")

class Note(Base):
    __tablename__ = "notes"
    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    instructor_id = Column(Integer, ForeignKey("instructors.id"), nullable=True)
    text = Column(Text, nullable=False)
    shared = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    player = relationship("Player", back_populates="notes")

class Drill(Base):
    __tablename__ = "drills"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    video_url = Column(String(500), nullable=True)  # can be file path or external URL
    created_at = Column(DateTime, default=datetime.utcnow)

class DrillAssignment(Base):
    __tablename__ = "drill_assignments"
    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    instructor_id = Column(Integer, ForeignKey("instructors.id"), nullable=True)
    drill_id = Column(Integer, ForeignKey("drills.id"), nullable=False)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    player = relationship("Player", back_populates="drills")
    drill = relationship("Drill")

class InstructorFavorite(Base):
    __tablename__ = "instructor_favorites"
    id = Column(Integer, primary_key=True, index=True)
    instructor_id = Column(Integer, ForeignKey("instructors.id"), nullable=False)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)

    instructor = relationship("Instructor", back_populates="favorites")
