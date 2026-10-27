from .lien_fader import LienFader
from .faith_turtle import FaithTurtle
from .raschke_soup import RaschkeSoup
from .bollinger_squeeze import BollingerSqueeze
from .crabel_nr4 import CrabelNR4
from .lien_zeros import LienDoubleZeros
from .williams_vbo import WilliamsVBO
from .ponsi_trend import PonsiTrend
from .brooks_range import BrooksRangeFade
from .tharp_random import TharpRandom
from .dalton_value import DaltonValueArea

ALL = [LienFader, FaithTurtle, RaschkeSoup, BollingerSqueeze, CrabelNR4,
       LienDoubleZeros, WilliamsVBO, PonsiTrend, BrooksRangeFade, TharpRandom, DaltonValueArea]
NEW6 = ["lien-zeros", "williams-vbo", "ponsi-trend", "brooks-range-fade", "tharp-random", "dalton-value"]
BY_SID = {c.sid: c for c in ALL}
