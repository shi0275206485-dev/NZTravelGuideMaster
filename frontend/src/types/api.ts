/**
 * Hand-written mirrors of the backend Pydantic models.
 *
 * These are a stopgap. Once the planning pipeline lands, regenerate them
 * from the live OpenAPI schema instead of maintaining them by hand:
 *
 *     npm run gen:types      # backend must be running on :8000
 *
 * Keeping the two in sync manually is exactly the drift this project's
 * single-source-of-truth data model is meant to prevent.
 */

export type TimeSlot = "morning" | "afternoon" | "evening";

export type TravelPreference =
  | "nature"
  | "culture"
  | "food"
  | "family"
  | "adventure"
  | "relaxation";

export type BudgetLevel = "budget" | "mid_range" | "premium";

export type AccommodationType = "hotel" | "motel" | "hostel" | "guest_house";

export interface Destination {
  name: string;
  lat: number;
  lon: number;
  tier: number;
}

export interface Location {
  lat: number;
  lon: number;
  address?: string | null;
}

export interface CostEstimate {
  low_nzd: number;
  high_nzd: number;
  basis: string;
}

export interface Attraction {
  id: string;
  osm_id?: string | null;
  name: string;
  category: string;
  location: Location;
  opening_hours?: string | null;
  has_wikidata: boolean;
  estimated_cost?: CostEstimate | null;
}

export interface ItineraryItem {
  attraction: Attraction;
  time_slot: TimeSlot;
  note: string;
}

export interface WeatherInfo {
  available: boolean;
  temp_min_c?: number | null;
  temp_max_c?: number | null;
  precipitation_probability?: number | null;
  summary?: string | null;
  is_wet: boolean;
}

export interface Meal {
  meal_type: "breakfast" | "lunch" | "dinner";
  suggestion: string;
  estimated_cost?: CostEstimate | null;
}

export interface RouteLeg {
  from_id: string;
  to_id: string;
  distance_m: number;
  duration_s: number;
  /** Encoded polyline for the whole day, present on the first leg only. */
  geometry?: string | null;
}

export interface DayPlan {
  day: number;
  date?: string | null;
  summary: string;
  items: ItineraryItem[];
  meals: Meal[];
  weather?: WeatherInfo | null;
  route_legs: RouteLeg[];
  /** Computed server-side from route_legs; null when the day is unrouted. */
  travel_distance_m: number;
  travel_duration_s: number;
  travel_summary?: string | null;
}

export interface Hotel {
  id: string;
  osm_id?: string | null;
  name: string;
  accommodation_type: AccommodationType;
  location: Location;
  stars?: number | null;
  estimated_cost_per_night?: CostEstimate | null;
}

export interface Budget {
  accommodation: CostEstimate;
  meals: CostEstimate;
  transport: CostEstimate;
  attractions: CostEstimate;
}

export interface TripPlan {
  destination: string;
  start_date: string;
  end_date: string;
  days: DayPlan[];
  hotel?: Hotel | null;
  budget?: Budget | null;
  /**
   * The level the trip was priced at. Sent back on every recompute — the
   * server has only the plan to work from, so dropping it here would have
   * the budget quietly revert to mid-range after an edit.
   */
  budget_level: BudgetLevel;
  weather_available: boolean;
  /** Shortlisted places the planner did not schedule — what editing offers. */
  alternatives: Attraction[];
  notes: string[];
}

export interface TripRequest {
  destination: string;
  start_date: string;
  end_date: string;
  preferences: TravelPreference[];
  budget_level: BudgetLevel;
  accommodation_type?: AccommodationType | null;
  /** Free-form constraints the structured fields cannot express. */
  free_text?: string | null;
}
